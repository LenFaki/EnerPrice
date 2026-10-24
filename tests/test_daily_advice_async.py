"""Async Daily Advisor worker regressions without a Home Assistant runtime."""

import ast
import asyncio
import logging
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from custom_components.nl_day_ahead_prices import const

PACKAGE = "custom_components.nl_day_ahead_prices"
SOURCE = Path(__file__).parents[1] / "custom_components/nl_day_ahead_prices/daily_sensor.py"
tree = ast.parse(SOURCE.read_text())
worker_node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "_AsyncDailyAdvice")
namespace = {
    "__name__": f"{PACKAGE}.daily_sensor",
    "__package__": PACKAGE,
    "asyncio": asyncio,
    "partial": partial,
    "timezone": timezone,
    "_LOGGER": logging.getLogger(__name__),
    "dt_util": SimpleNamespace(utcnow=lambda: datetime.now(timezone.utc)),
    "CONF_ADVICE_LANGUAGE": const.CONF_ADVICE_LANGUAGE,
    "CONF_FLEXIBLE_LOAD_POWER_W": const.CONF_FLEXIBLE_LOAD_POWER_W,
}
exec(compile(ast.Module(body=[worker_node], type_ignores=[]), str(SOURCE), "exec"), namespace)
_AsyncDailyAdvice = namespace["_AsyncDailyAdvice"]


@pytest.fixture
def dependencies():
    advisor = ModuleType(f"{PACKAGE}.daily_advisor")
    advisor.advisor_context = Mock(return_value=None)
    models = ModuleType(f"{PACKAGE}.models")
    models.PriceEntry = lambda time, price: SimpleNamespace(time=time, price=price)
    sensor = ModuleType(f"{PACKAGE}.sensor")
    sensor._cached_all_in_entries = Mock(return_value=[])
    smart = ModuleType(f"{PACKAGE}.smart_energy")
    smart.build_smart_energy_advice = Mock(return_value={"daily_advisor": {"state": "ready"}})
    with patch.dict("sys.modules", {
        advisor.__name__: advisor,
        models.__name__: models,
        sensor.__name__: sensor,
        smart.__name__: smart,
    }):
        yield smart


def make_worker():
    hass = SimpleNamespace(
        async_create_task=asyncio.create_task,
        async_add_executor_job=None,
        config=SimpleNamespace(language="nl"),
    )
    coordinator = SimpleNamespace(
        hass=hass,
        entry=SimpleNamespace(data={}, options={}),
        data=None,
    )
    worker = _AsyncDailyAdvice(coordinator)
    sensor = Mock()
    return worker, coordinator, sensor


@pytest.mark.asyncio
async def test_calculation_is_deferred_and_result_published(dependencies):
    worker, coordinator, sensor = make_worker()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def executor(fn):
        entered.set()
        await release.wait()
        return fn()

    coordinator.hass.async_add_executor_job = executor
    worker.add(sensor)
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        assert worker.result is None
        sensor.async_write_ha_state.assert_not_called()
    finally:
        release.set()
    await asyncio.wait_for(worker.task, timeout=3)
    assert worker.result["state"] == "ready"
    sensor.async_write_ha_state.assert_called_once_with()


@pytest.mark.asyncio
async def test_refresh_drops_stale_result(dependencies):
    worker, coordinator, sensor = make_worker()
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def executor(fn):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
            return {"daily_advisor": {"state": "stale"}}
        return {"daily_advisor": {"state": "fresh"}}

    coordinator.hass.async_add_executor_job = executor
    worker.add(sensor)
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        worker.request()
    finally:
        release.set()
    await asyncio.wait_for(worker.task, timeout=3)
    assert calls == 2
    assert worker.result["state"] == "fresh"
    sensor.async_write_ha_state.assert_called_once_with()


@pytest.mark.asyncio
async def test_unload_cancels_background_task(dependencies):
    worker, coordinator, sensor = make_worker()
    entered = asyncio.Event()

    async def executor(fn):
        entered.set()
        await asyncio.Event().wait()

    coordinator.hass.async_add_executor_job = executor
    worker.add(sensor)
    await asyncio.wait_for(entered.wait(), timeout=3)
    task = worker.task
    worker.remove(sensor)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3)
    assert worker.result is None
    sensor.async_write_ha_state.assert_not_called()
