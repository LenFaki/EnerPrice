"""Regression tests for sharing the daily plan with the live Smart Energy Advisor."""

import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

from custom_components.nl_day_ahead_prices.models import PriceEntry
from tests.test_sensor_attributes import sensor_module  # noqa: F401 - pytest fixture

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def _sensor(module, monkeypatch, *, with_forecast):
    entry = SimpleNamespace(
        entry_id="test", data={}, options={"smart_advice_language": "nl"},
    )
    coordinator = SimpleNamespace(
        data=SimpleNamespace(), daily_forecast=SimpleNamespace(data={}) if with_forecast else None,
    )
    sensor = module.NLSmartEnergyAdvisorSensor(coordinator, entry)
    sensor.hass = SimpleNamespace(config=SimpleNamespace(language="nl"))
    monkeypatch.setattr(module.dt_util, "now", lambda: NOW)
    monkeypatch.setattr(
        module, "_cached_all_in_entries",
        lambda *_: [PriceEntry(NOW - timedelta(minutes=15), 0.25)],
    )
    monkeypatch.setattr(module, "_state_float_sensor", lambda *_: None)
    return sensor


def test_live_advisor_uses_shared_daily_plan_without_rebuilding_it(sensor_module, monkeypatch):
    daily_payload = {"state": "ready", "chart": [{"time": "unchanged"}]}
    daily = ModuleType("custom_components.nl_day_ahead_prices.daily_advisor")
    daily.daily_advice = Mock(return_value=daily_payload)
    monkeypatch.setitem(sys.modules, daily.__name__, daily)

    live_builder = Mock(side_effect=[
        {"state": "gas", "recommendation": "First reading"},
        {"state": "cheap_grid", "recommendation": "New reading"},
    ])
    monkeypatch.setattr(sensor_module, "build_smart_energy_advice", live_builder)
    sensor = _sensor(sensor_module, monkeypatch, with_forecast=True)

    first = sensor._current_result()
    assert first["daily_advisor"] is daily_payload
    assert sensor.extra_state_attributes["daily_advisor"] is daily_payload
    assert live_builder.call_count == 1  # native state + attributes share one live calculation
    assert "daily_context" not in live_builder.call_args.kwargs
    daily.daily_advice.assert_called_once_with(sensor.coordinator, language="nl")

    # A new live input must recalculate the live advice but reuse the shared
    # daily plan, rather than independently rebuilding a three-day chart.
    sensor._result = None
    second = sensor._current_result()
    assert second["state"] == "cheap_grid"
    assert second["daily_advisor"] is daily_payload
    assert live_builder.call_count == 2
    assert daily.daily_advice.call_count == 2


def test_live_advisor_without_daily_forecast_keeps_legacy_behavior(sensor_module, monkeypatch):
    daily = ModuleType("custom_components.nl_day_ahead_prices.daily_advisor")
    daily.daily_advice = Mock(side_effect=AssertionError("No daily forecast configured"))
    monkeypatch.setitem(sys.modules, daily.__name__, daily)
    builder = Mock(return_value={"state": "normal"})
    monkeypatch.setattr(sensor_module, "build_smart_energy_advice", builder)

    sensor = _sensor(sensor_module, monkeypatch, with_forecast=False)
    assert sensor._current_result() == {"state": "normal"}
    assert "daily_context" not in builder.call_args.kwargs
    daily.daily_advice.assert_not_called()
