"""Regression coverage for asynchronous Daily Advisor sensor calculations."""

import asyncio
import importlib.util
import sys

from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import custom_components.nl_day_ahead_prices as integration_package

# The lightweight CI suite does not install Home Assistant. Stub only the
# entity types needed to import the worker; exercise the real worker logic.
sensor_module = ModuleType("homeassistant.components.sensor")
sensor_module.SensorDeviceClass = SimpleNamespace(ENUM="enum", TIMESTAMP="timestamp", ENERGY="energy")
sensor_module.SensorEntity = type("SensorEntity", (), {})
const_module = ModuleType("homeassistant.const")
const_module.EntityCategory = SimpleNamespace(DIAGNOSTIC="diagnostic")
const_module.UnitOfEnergy = SimpleNamespace(KILO_WATT_HOUR="kWh")
coordinator_module = ModuleType("homeassistant.helpers.update_coordinator")
coordinator_module.CoordinatorEntity = type("CoordinatorEntity", (), {})
util_module = ModuleType("homeassistant.util")
util_module.dt = SimpleNamespace(utcnow=lambda: datetime.now(timezone.utc))

with patch.dict(sys.modules, {
    "homeassistant.components.sensor": sensor_module,
    "homeassistant.const": const_module,
    "homeassistant.helpers.update_coordinator": coordinator_module,
    "homeassistant.util": util_module,
    "custom_components.nl_day_ahead_prices.daily_advisor": ModuleType("custom_components.nl_day_ahead_prices.daily_advisor"),
}):
    sys.modules["custom_components.nl_day_ahead_prices.daily_advisor"].advisor_context = lambda coordinator: None
    spec = importlib.util.spec_from_file_location(
        "custom_components.nl_day_ahead_prices.daily_sensor",
        Path(__file__).parents[1] / "custom_components/nl_day_ahead_prices/daily_sensor.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _AsyncDailyAdvice = module._AsyncDailyAdvice

# patch() resolves package attributes, not only sys.modules entries.
integration_package.daily_advisor = sys.modules["custom_components.nl_day_ahead_prices.daily_advisor"]


def make_worker():
    hass = SimpleNamespace(
        async_create_task=lambda coroutine: asyncio.create_task(coroutine),
        async_add_executor_job=None,
        config=SimpleNamespace(language="nl"),
    )
    coordinator = SimpleNamespace(
        hass=hass,
        entry=SimpleNamespace(data={}, options={}),
        data=None,
        daily_forecast=SimpleNamespace(data={}),
    )
    worker = _AsyncDailyAdvice(coordinator)
    sensor = SimpleNamespace(async_write_ha_state=Mock())
    return worker, coordinator, sensor


@pytest.mark.asyncio
async def test_advisor_computes_off_event_loop_and_publishes_result():
    worker, coordinator, sensor = make_worker()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def executor(fn):
        entered.set()
        await release.wait()
        return fn()

    coordinator.hass.async_add_executor_job = executor
    with (
        patch("custom_components.nl_day_ahead_prices.daily_advisor.advisor_context", return_value=None),
        patch("custom_components.nl_day_ahead_prices.smart_energy.build_smart_energy_advice",
              return_value={"daily_advisor": {"state": "ready", "best_window": None}}),
    ):
        worker.add(sensor)
        await entered.wait()
        assert worker.result is None
        assert sensor.async_write_ha_state.call_count == 0
        release.set()
        await worker.task
    assert worker.result["state"] == "ready"
    sensor.async_write_ha_state.assert_called_once_with()


@pytest.mark.asyncio
async def test_stale_advice_is_discarded_after_new_request():
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
    with patch("custom_components.nl_day_ahead_prices.daily_advisor.advisor_context", return_value=None):
        worker.add(sensor)
        await entered.wait()
        worker.request()
        release.set()
        await worker.task
    assert calls == 2
    assert worker.result["state"] == "fresh"
    sensor.async_write_ha_state.assert_called_once_with()


@pytest.mark.asyncio
async def test_unload_cancels_pending_advice_without_publishing():
    worker, coordinator, sensor = make_worker()
    entered = asyncio.Event()

    async def executor(fn):
        entered.set()
        await asyncio.Event().wait()

    coordinator.hass.async_add_executor_job = executor
    with patch("custom_components.nl_day_ahead_prices.daily_advisor.advisor_context", return_value=None):
        worker.add(sensor)
        await entered.wait()
        task = worker.task
        worker.remove(sensor)
        with pytest.raises(asyncio.CancelledError):
            await task
    assert worker.result is None
    sensor.async_write_ha_state.assert_not_called()
