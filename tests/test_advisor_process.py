"""Actual spawned-process tests: IPC, CPU/GC isolation, failure and termination."""

import asyncio
import gc
import os
import time
from dataclasses import replace
from datetime import timedelta

import pytest

from custom_components.nl_day_ahead_prices.advisor_process import AdvisorProcess
from custom_components.nl_day_ahead_prices.calculations import calculate_all_in_price
from custom_components.nl_day_ahead_prices.const import CONF_FLEXIBLE_LOAD_POWER_W
from custom_components.nl_day_ahead_prices.daily_runtime import calculate_snapshot
from custom_components.nl_day_ahead_prices.supplier_profiles import normalize_supplier_profile
from custom_components.nl_day_ahead_prices.supplier_registry import market_date
from tests.test_daily_advice import NOW, context, prices_for_day


def snapshot():
    prices = prices_for_day(minutes=15)
    profile = normalize_supplier_profile({"name": "Custom"})
    tariffs = {market_date(item.time): profile for item in prices}
    return (
        (1, profile, .1, .21, tariffs), tuple(prices), profile, .1, .21,
        context(prices, minutes=15)["forecast"], {CONF_FLEXIBLE_LOAD_POWER_W: 1000},
        NOW, "nl", 15, tariffs,
    )


def cpu_and_gc_worker(connection):
    try:
        connection.send({"ready": True})
        connection.recv()
        # Exercise a genuinely separate GIL and GC graph, not a sleeping thread.
        started = time.thread_time()
        garbage = []
        for _ in range(40_000):
            cycle = []
            cycle.append(cycle)
            garbage.append(cycle)
        del garbage, cycle
        collected = gc.collect(2)
        while time.thread_time() - started < .5:
            sum(range(1000))
        connection.send({"ok": True, "result": {"pid": os.getpid(), "collected": collected}})
    finally:
        connection.close()


def stalled_worker(connection):
    connection.send({"ready": True})
    connection.recv()
    time.sleep(30)


def crashed_worker(connection):
    connection.send({"ready": True})
    connection.recv()
    os._exit(2)


async def test_real_process_reuses_pid_and_preserves_advice():
    client = AdvisorProcess()
    try:
        inputs = snapshot()
        expected, _ = calculate_snapshot(inputs, None)
        first = await asyncio.to_thread(client.calculate, inputs)
        pid = client._process.pid
        assert pid != os.getpid()
        assert first == expected
        second = await asyncio.to_thread(client.calculate, inputs)
        assert client._process.pid == pid
        assert second == first
    finally:
        await asyncio.to_thread(client.close)
    assert client._process is None and client._connection is None


async def test_cpu_bound_child_and_full_gc_do_not_stall_parent_event_loop():
    client = AdvisorProcess(worker_target=cpu_and_gc_worker)
    try:
        task = asyncio.create_task(asyncio.to_thread(client.calculate, None))
        ticks = []
        while not task.done():
            ticks.append(time.perf_counter())
            await asyncio.sleep(.01)
        result = await task
        assert result["pid"] != os.getpid()
        assert result["collected"] >= 39_999
        assert len(ticks) >= 15
        assert max(b - a for a, b in zip(ticks, ticks[1:], strict=False)) < .25
    finally:
        await asyncio.to_thread(client.close)


async def test_remote_and_future_date_tariffs_survive_spawn_without_parent_globals():
    client = AdvisorProcess()
    try:
        inputs = list(snapshot())
        prices = [*inputs[1], *prices_for_day(NOW + timedelta(days=1), minutes=15)]
        base = replace(normalize_supplier_profile({"name": "Remote"}), key="zonneplan", registry_source="remote")
        first_day = replace(base, purchase_fee_electricity=.02, purchase_fee_import=.02)
        second_day = replace(base, purchase_fee_electricity=.08, purchase_fee_import=.08)
        tariffs = {NOW.date(): first_day, (NOW + timedelta(days=1)).date(): second_day}
        inputs[0] = (2, base, .1, .21, tariffs)
        inputs[1], inputs[2], inputs[10] = tuple(prices), base, tariffs
        result = await asyncio.to_thread(client.calculate, tuple(inputs))
        assert result["days"][0]["window"]["average_price_eur_kwh"] == round(calculate_all_in_price(.3, .1, first_day, .21), 6)
        assert result["days"][1]["window"]["average_price_eur_kwh"] == round(calculate_all_in_price(.3, .1, second_day, .21), 6)
    finally:
        await asyncio.to_thread(client.close)


async def test_timeout_terminates_child_and_releases_pipe():
    client = AdvisorProcess(timeout=.3, worker_target=stalled_worker)
    try:
        with pytest.raises(TimeoutError):
            await asyncio.to_thread(client.calculate, snapshot())
        assert client._process is None and client._connection is None
    finally:
        await asyncio.to_thread(client.close)


async def test_child_crash_does_not_fall_back_to_ha_thread():
    client = AdvisorProcess(worker_target=crashed_worker)
    try:
        with pytest.raises((EOFError, ConnectionError)):
            await asyncio.to_thread(client.calculate, snapshot())
        assert client._process is None and client._connection is None
    finally:
        await asyncio.to_thread(client.close)


async def test_close_interrupts_an_inflight_job_and_prevents_restart():
    client = AdvisorProcess(worker_target=stalled_worker)
    task = asyncio.create_task(asyncio.to_thread(client.calculate, snapshot()))
    # Wait for actual spawn, not for the deliberately stalled response.
    for _ in range(200):
        if client._process is not None:
            break
        await asyncio.sleep(.01)
    assert client._process is not None
    await asyncio.to_thread(client.close)
    with pytest.raises((EOFError, OSError, RuntimeError)):
        await asyncio.wait_for(task, 5)
    with pytest.raises(RuntimeError, match="closed"):
        await asyncio.to_thread(client.calculate, snapshot())
    assert client._process is None


async def test_child_calculation_error_is_bounded_and_next_input_can_restart():
    client = AdvisorProcess()
    try:
        invalid = list(snapshot())
        invalid[9] = 7  # Invalid interval resolution.
        with pytest.raises(RuntimeError, match="calculation failed"):
            await asyncio.to_thread(client.calculate, tuple(invalid))
        assert client._process is None
        assert (await asyncio.to_thread(client.calculate, snapshot()))["state"] == "ready"
    finally:
        await asyncio.to_thread(client.close)


def failed_bootstrap_worker(connection):
    connection.send({"ready": False, "error": "ModuleNotFoundError"})
    connection.close()


async def test_startup_failure_reports_bootstrap_error_and_cleans_up():
    client = AdvisorProcess(worker_target=failed_bootstrap_worker)
    try:
        with pytest.raises(RuntimeError, match="bootstrap failed.*ModuleNotFoundError"):
            await asyncio.to_thread(client.calculate, snapshot())
        assert client._process is None and client._connection is None
    finally:
        await asyncio.to_thread(client.close)


def test_spawn_from_ha_style_temporary_import_path(tmp_path):
    """HA mounts custom_components then removes its config directory from sys.path.

    Run outside the checkout, without PYTHONPATH and without reimporting this
    project in the child's __mp_main__. The beta.12 target fails during unpickle
    here, exactly matching the field ConnectionResetError on Python 3.14.
    """
    import json
    import subprocess
    import sys
    from pathlib import Path

    driver = tmp_path / "ha_style_driver.py"
    driver.write_text('''
import json
import os
import sys

if __name__ == "__main__":
    root = sys.argv[1]
    sys.path.insert(0, root)
    from datetime import datetime, timedelta, timezone
    from custom_components.nl_day_ahead_prices.advisor_process import AdvisorProcess
    from custom_components.nl_day_ahead_prices.models import PriceEntry
    from custom_components.nl_day_ahead_prices.supplier_profiles import normalize_supplier_profile
    from custom_components.nl_day_ahead_prices.supplier_registry import market_date
    profile = normalize_supplier_profile({"name": "Custom"})
    now = datetime(2026, 11, 19, 0, tzinfo=timezone.utc)
    prices = tuple(PriceEntry(now + timedelta(hours=i), .3) for i in range(48))
    tariffs = {market_date(row.time): profile for row in prices}
    inputs = ((1, profile, .1, .21, tariffs), prices, profile, .1, .21, {}, {}, now, "en", 60, tariffs)
    sys.path.remove(root)
    before = tuple(sys.path)
    client = AdvisorProcess()
    try:
        result = client.calculate(inputs)
        pid = client._process.pid
        again = client.calculate(inputs)
        assert client._process.pid == pid
        assert result == again
        assert tuple(sys.path) == before
        print(json.dumps({"state": result["state"], "pid": pid, "parent": os.getpid()}))
    finally:
        client.close()
''')
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    root = Path(__file__).parents[1]
    result = subprocess.run(
        [sys.executable, str(driver), str(root)], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=25, check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["state"] == "ready"
    assert payload["pid"] != payload["parent"]


async def test_powerpeers_confirmed_contract_survives_real_spawn():
    from custom_components.nl_day_ahead_prices.contract_pricing import powerpeers_profile
    from tests.test_powerpeers_contract import contract

    inputs = list(snapshot())
    profile = powerpeers_profile(contract(custom_purchase_fee_electricity=.03))
    tariffs = {market_date(item.time): profile for item in inputs[1]}
    inputs[0] = (3, profile, .1, .21, tariffs)
    inputs[2], inputs[10] = profile, tariffs
    expected, _ = calculate_snapshot(tuple(inputs), None)
    client = AdvisorProcess()
    try:
        actual = await asyncio.to_thread(client.calculate, tuple(inputs))
        assert actual == expected
        assert actual["days"][0]["window"]["average_price_eur_kwh"] == round(.3 * 1.21 + .1 + .03, 6)
    finally:
        await asyncio.to_thread(client.close)
