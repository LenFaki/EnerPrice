"""Freshness, concurrency, thread ownership and lifetime of prepared daily advice."""

import asyncio
import threading
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.nl_day_ahead_prices import daily_runtime as module
from custom_components.nl_day_ahead_prices.const import CONF_DAILY_DURATION_MINUTES, CONF_FLEXIBLE_LOAD_POWER_W
from tests.test_daily_advice import (
    NOW,
    context,
    daily,
    prices_for_day,
)
from tests.test_daily_advice import advice_runtime as _advice_runtime_fixture


@pytest.fixture(name="advice_runtime")
def _shared_advice_runtime(monkeypatch):
    return _advice_runtime_fixture.__wrapped__(monkeypatch)


class _InlineWorker:
    """Exercise scheduling independently of the separately tested process transport."""

    def __init__(self):
        self.converted = None

    def calculate(self, snapshot):
        result, self.converted = module.calculate_snapshot(snapshot, self.converted)
        return result

    def close(self):
        self.converted = None


@pytest.fixture
def runtime(monkeypatch):
    prices = prices_for_day(minutes=15)
    ctx = context(prices, minutes=15)
    from custom_components.nl_day_ahead_prices.supplier_profiles import normalize_supplier_profile

    profile = normalize_supplier_profile({"name": "Custom"})
    clock = [NOW]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0].timestamp())
    entry = SimpleNamespace(data={}, options={CONF_FLEXIBLE_LOAD_POWER_W: 1000})
    entry.async_create_background_task = lambda hass, coro, name: asyncio.create_task(coro)
    hass = SimpleNamespace(config=SimpleNamespace(language="en"))
    hass.async_add_executor_job = asyncio.to_thread
    coordinator = SimpleNamespace(
        entry=entry, hass=hass, data=object(),
        daily_forecast=SimpleNamespace(data=ctx["forecast"]), async_update_listeners=Mock(),
    )
    worker = module.DailyAdviceRuntime(coordinator, lambda: clock[0])
    worker._worker = _InlineWorker()

    def snapshot(language):
        data = coordinator.data
        options = {**entry.data, **entry.options}
        return (
            (id(data), profile, 0., 0.), tuple(prices), profile, 0., 0.,
            coordinator.daily_forecast.data, options, clock[0], language, 15,
            {module.market_date(item.time): profile for item in prices},
        ), data

    monkeypatch.setattr(worker, "_snapshot", snapshot)
    return SimpleNamespace(worker=worker, coordinator=coordinator, prices=prices, context=ctx, clock=clock)


async def test_property_read_never_builds_and_shared_worker_runs_off_loop(runtime, monkeypatch):
    worker = runtime.worker
    main_thread = threading.get_ident()
    original = module.calculate_snapshot
    threads = []

    def build(*args):
        threads.append(threading.get_ident())
        return original(*args)

    monkeypatch.setattr(module, "calculate_snapshot", build)
    first = worker.get()
    task = worker._task
    assert first["state"] == "unavailable"
    assert worker.get() is first
    assert worker._task is task
    assert threads == []
    result = await worker.async_get()
    assert result["state"] == "ready"
    assert worker.get() is result
    assert len(threads) == 1 and threads[0] != main_thread
    runtime.coordinator.async_update_listeners.assert_called_once()
    await worker.async_stop()


async def test_changed_measurement_keeps_tariff_conversions_but_updates_solar(runtime, monkeypatch):
    exports = Mock(wraps=module.export_entries_for_supplier)
    monkeypatch.setattr(module, "export_entries_for_supplier", exports)
    before = await runtime.worker.async_get()
    assert before["assumed_household_load_w"] == 500
    forecast = runtime.coordinator.daily_forecast.data
    runtime.coordinator.daily_forecast.data = {
        **forecast, "measurements": [
            {**forecast["measurements"][0], "value": 1000}, forecast["measurements"][1],
        ],
    }
    after = await runtime.worker.async_get()
    assert after["assumed_household_load_w"] == 3000
    assert exports.call_count == 1
    assert after is not before
    await runtime.worker.async_stop()


@pytest.mark.parametrize("change", ["prices", "duration", "clock", "language"])
async def test_real_inputs_invalidate_plan(runtime, change):
    worker = runtime.worker
    before = await worker.async_get()
    if change == "prices":
        runtime.coordinator.data = object()
        runtime.prices[:] = [module.PriceEntry(row.time, -.1) for row in runtime.prices]
    elif change == "duration":
        runtime.coordinator.entry.options[CONF_DAILY_DURATION_MINUTES] = 120
    elif change == "clock":
        runtime.clock[0] += timedelta(minutes=5)
    else:
        runtime.coordinator.hass.config.language = "nl"
    after = await worker.async_get()
    assert after is not before
    if change == "prices":
        assert after["best_window"]["average_price_eur_kwh"] == -.1
    if change == "duration":
        assert after["best_window"]["duration_minutes"] == 120
    if change == "language":
        assert "Plan alleen" in after["summary"]
    await worker.async_stop()


async def test_old_job_cannot_publish_after_new_measurement_arrives(runtime):
    worker = runtime.worker
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def executor(job):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
        return await asyncio.to_thread(job)

    runtime.coordinator.hass.async_add_executor_job = executor
    worker.get()
    await started.wait()
    forecast = runtime.coordinator.daily_forecast.data
    runtime.coordinator.daily_forecast.data = {
        **forecast, "measurements": [
            {**forecast["measurements"][0], "value": 1000}, forecast["measurements"][1],
        ],
    }
    worker.get()
    release.set()
    result = await worker.async_get()
    assert result["assumed_household_load_w"] == 3000
    assert calls == 2
    runtime.coordinator.async_update_listeners.assert_called_once()
    await worker.async_stop()


async def test_unload_cancels_pending_publish(runtime):
    started, release = asyncio.Event(), asyncio.Event()

    async def executor(job):
        if getattr(job, "__name__", "") == "close":
            return job()
        started.set()
        await release.wait()
        return job()

    runtime.coordinator.hass.async_add_executor_job = executor
    runtime.worker.get()
    await started.wait()
    await runtime.worker.async_stop()
    release.set()
    assert runtime.worker._task is None
    assert runtime.worker.get()["state"] == "unavailable"
    runtime.coordinator.async_update_listeners.assert_not_called()


async def test_failure_is_reported_once_and_retries_only_after_inputs_change(runtime, monkeypatch, caplog):
    builder = Mock(side_effect=ValueError("invalid forecast"))
    monkeypatch.setattr(module, "calculate_snapshot", builder)
    assert (await runtime.worker.async_get())["state"] == "unavailable"
    for _ in range(5):
        runtime.worker.get()
    assert runtime.worker._task is None and builder.call_count == 1
    assert "Daily Advisor calculation failed" in caplog.text
    runtime.clock[0] += timedelta(minutes=5)
    await runtime.worker.async_get()
    assert builder.call_count == 2
    await runtime.worker.async_stop()


async def test_worker_matches_existing_advice_without_changing_financial_model(runtime):
    result = await runtime.worker.async_get()
    ctx = runtime.context
    # Custom profile has zero export fee, so its export price is the market price.
    ctx["export_prices"] = runtime.prices
    assert result == daily(runtime.prices, ctx)
    await runtime.worker.async_stop()


async def test_real_ha_snapshot_is_read_on_loop_and_results_match_sync(advice_runtime):
    fixture = advice_runtime
    worker = module.DailyAdviceRuntime(fixture.coordinator, lambda: NOW)
    fixture.coordinator.entry.async_create_background_task = lambda hass, coro, name: asyncio.create_task(coro)
    fixture.coordinator.hass.async_add_executor_job = asyncio.to_thread
    fixture.coordinator.async_update_listeners = Mock()
    expected = fixture.adapter.module.daily_advice(fixture.coordinator)
    fixture.coordinator.daily_advice_runtime = worker
    result = await fixture.adapter.module.async_daily_advice(fixture.coordinator)
    assert result == expected
    assert fixture.adapter.module.daily_advice(fixture.coordinator) is result
    await worker.async_stop()


async def test_process_failure_backoff_applies_across_frequent_measurement_changes(runtime, monkeypatch):
    builder = Mock(side_effect=OSError("process cannot start"))
    monkeypatch.setattr(module, "calculate_snapshot", builder)
    await runtime.worker.async_get()
    for _ in range(20):
        runtime.coordinator.daily_forecast.data = {**runtime.coordinator.daily_forecast.data, "changed": _}
        await runtime.worker.async_get()
    assert builder.call_count == 1
    runtime.clock[0] += timedelta(seconds=61)
    await runtime.worker.async_get()
    assert builder.call_count == 2
    await runtime.worker.async_stop()
