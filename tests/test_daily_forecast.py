"""Daily Advisor PR1: forecasts, opt-in configuration and HA lifecycle contracts."""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.nl_day_ahead_prices import _requires_reload
from custom_components.nl_day_ahead_prices.const import (
    CONF_DAILY_ADVISOR_ENABLED,
    CONF_DAILY_CONSUMPTION_ENTITIES,
    CONF_DAILY_FORECAST_ENTITIES,
    CONF_DAILY_GRID_POWER_ENTITY,
    CONF_DAILY_SOLAR_ENERGY_ENTITY,
    DAILY_INPUT_KEYS,
)
from custom_components.nl_day_ahead_prices.solar_forecast import (
    AMSTERDAM,
    ENERGY_UNITS,
    MAX_SERIES_POINTS,
    POWER_UNITS,
    ForecastSource,
    forecast_snapshot,
    forecast_source,
    numeric_state,
    parse_forecast_response,
)
from tests.test_registry_ux import _load_config_flow_for_test, _smart_setup_flow

ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)


def state(value="10", *, unit="kWh", kind="energy", updated=NOW, **attrs):
    return SimpleNamespace(state=value, attributes={"device_class": kind, "unit_of_measurement": unit, **attrs}, last_updated=updated)


def registered(installation="roof", role="energy_production_today", platform="forecast_solar"):
    return SimpleNamespace(platform=platform, config_entry_id=installation, unique_id=f"{installation}_{role}")


def source(entity="sensor.today", installation="roof", offset=0):
    return ForecastSource(entity, installation, offset)


@pytest.mark.parametrize("role,offset", [("energy_production_today", 0), ("energy_production_tomorrow", 1)])
def test_forecast_identity_survives_entity_rename(role, offset):
    assert forecast_source("sensor.custom_name", registered(role=role)) == source("sensor.custom_name", offset=offset)


@pytest.mark.parametrize("entry", [None, registered(role="energy_production_today_remaining"), registered(role="energy_next_hour"), registered(platform="template")])
def test_unsupported_or_partial_total_identity_rejected(entry):
    assert forecast_source("sensor.foo", entry) is None


@pytest.mark.parametrize("value,unit,kind,expected", [
    ("1000", "Wh", "energy", 1), ("1.25", "kWh", "energy", 1.25),
    ("-1.5", "kW", "power", -1500), ("120", "W", "power", 120),
    ("unavailable", "Wh", "energy", None), ("unknown", "Wh", "energy", None),
    ("NaN", "kWh", "energy", None), ("inf", "kWh", "energy", None),
    (True, "kWh", "energy", None), ("2", "MWh", "energy", None),
])
def test_explicit_numeric_units(value, unit, kind, expected):
    assert numeric_state(state(value, unit=unit, kind=kind), kind, ENERGY_UNITS if kind == "energy" else POWER_UNITS) == expected


def test_multiple_installations_and_complete_days():
    sources = [source(), source("sensor.other", "other"), source("sensor.tomorrow", offset=1), source("sensor.other_tomorrow", "other", 1)]
    states = {item.entity_id: state("1500", unit="Wh") for item in sources}
    result = forecast_snapshot(sources, states, NOW)
    assert [day["energy_kwh"] for day in result["days"]] == [3, 3, None]
    assert result["days"][2]["status"] == "missing"
    assert result["solar_surplus_kwh"] is None


def test_duplicate_installation_does_not_double_count():
    sources = [source(), source("sensor.alias")]
    result = forecast_snapshot(sources, {item.entity_id: state() for item in sources}, NOW)
    assert result["days"][0]["energy_kwh"] is None
    assert "duplicate_installation_day" in result["issues"]


def test_missing_installation_never_returns_partial_sum_as_total():
    result = forecast_snapshot([source(), source("sensor.offline", "second")], {"sensor.today": state()}, NOW)
    assert result["days"][0]["energy_kwh"] is None
    assert result["days"][0]["status"] == "incomplete"


@pytest.mark.parametrize("bad", [None, state("unknown"), state("-1"), state(updated=NOW - timedelta(hours=3)), state(updated=NOW + timedelta(minutes=1)), state(restored=True), state(kind="power"), state(updated=NOW.replace(tzinfo=None))])
def test_unknown_stale_future_restored_and_wrong_units_are_not_zero(bad):
    result = forecast_snapshot([source()], {"sensor.today": bad}, NOW)
    assert result["days"][0]["energy_kwh"] is None
    assert result["quality"] == "unavailable"


def test_midnight_does_not_promote_yesterday_relative_states():
    now = datetime(2026, 10, 13, 0, 5, tzinfo=AMSTERDAM)
    old = state(updated=now - timedelta(minutes=10))
    result = forecast_snapshot([source()], {"sensor.today": old}, now)
    assert result["days"][0]["date"] == "2026-10-13"
    assert result["days"][0]["energy_kwh"] is None
    assert "date_rollover_pending" in result["issues"]


@pytest.mark.parametrize("year,month,day,hours", [(2026, 3, 29, 23), (2026, 10, 25, 25), (2026, 10, 12, 24)])
def test_calendar_days_and_quarter_hours_across_dst(year, month, day, hours):
    now = datetime(year, month, day, tzinfo=AMSTERDAM)
    start = now.astimezone(timezone.utc)
    stamps = [(start + timedelta(minutes=15*i)).astimezone(AMSTERDAM).isoformat() for i in range(hours*4)]
    response = {"watts": dict.fromkeys(stamps, 400), "wh_period": dict.fromkeys(stamps, 100)}
    result = parse_forecast_response(response, now)
    assert len(result) == hours * 4
    assert sum(item["energy_kwh"] for item in result) == pytest.approx(hours * 0.4)
    assert len({item["time"] for item in result}) == hours * 4
    snapshot = forecast_snapshot([], {}, now)
    assert [item["date"] for item in snapshot["days"]] == [(now.date()+timedelta(days=i)).isoformat() for i in range(3)]


@pytest.mark.parametrize("response", [
    {}, {"watts": [], "wh_period": {}},
    {"watts": {"2026-10-12T12:00:00": 100}, "wh_period": {}},
    {"watts": {"2026-10-12T12:00:00+02:00": True}, "wh_period": {}},
    {"watts": {"2026-10-12T12:00:00+02:00": float("nan")}, "wh_period": {}},
    {"watts": {}, "wh_period": {"2026-10-12T12:00:00+02:00": -1}},
    {"watts": {str(i): 1 for i in range(MAX_SERIES_POINTS+1)}, "wh_period": {}},
    {"watts": {"2026-10-12T12:00:00+02:00": 1, "2026-10-12T10:00:00Z": 1}, "wh_period": {}},
])
def test_service_rejects_unverified_schema_numbers_and_unbounded_arrays(response):
    with pytest.raises(ValueError):
        parse_forecast_response(response, NOW)


def test_series_clipped_to_three_dates_without_inventing_energy_or_prices():
    stamps = [(NOW + timedelta(days=i)).isoformat() for i in range(-1, 5)]
    points = parse_forecast_response({"watts": dict.fromkeys(stamps, 400), "wh_period": {}}, NOW)
    assert len(points) == 3
    assert all(point["energy_kwh"] is None for point in points)
    assert all("price" not in point for point in points)


def load_adapter(monkeypatch):
    """HA boundary doubles: async state reads, service responses and cancellation."""
    class Coordinator:
        def __init__(self, hass, *args, **kwargs):
            self.hass = hass
            self.update_interval = kwargs["update_interval"]
            self.async_shutdown = AsyncMock()
            self.async_update_listeners = Mock()

        def async_set_updated_data(self, data):
            self.data = data

    clock = SimpleNamespace(now=NOW)
    track = Mock(return_value=Mock())
    modules = {
        "homeassistant.core": {"callback": lambda function: function},
        "homeassistant.exceptions": {"HomeAssistantError": RuntimeError},
        "homeassistant.helpers": {},
        "homeassistant.helpers.entity_registry": {"async_get": lambda hass: hass.registry},
        "homeassistant.helpers.event": {"async_track_state_change_event": track, "async_track_utc_time_change": Mock(return_value=Mock())},
        "homeassistant.helpers.update_coordinator": {"DataUpdateCoordinator": Coordinator},
        "homeassistant.util": {"dt": SimpleNamespace(utcnow=lambda: clock.now)},
    }
    for name, attrs in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    name = "custom_components.nl_day_ahead_prices.daily_advisor"
    spec = importlib.util.spec_from_file_location(name, ROOT / "custom_components/nl_day_ahead_prices/daily_advisor.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    states = {"sensor.today": state(), "sensor.tomorrow": state("12"), "sensor.grid": state("-1.5", kind="power", unit="kW")}
    registry = {"sensor.today": registered(), "sensor.tomorrow": registered(role="energy_production_tomorrow")}
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get), registry=SimpleNamespace(async_get=registry.get),
        services=SimpleNamespace(async_services=lambda: {}, has_service=Mock(return_value=True), async_call=AsyncMock(return_value={"watts": {NOW.isoformat(): 100}, "wh_period": {NOW.isoformat(): 50}})),
    )
    entry = SimpleNamespace(entry_id="test", data={}, options={CONF_DAILY_FORECAST_ENTITIES: ["sensor.today", "sensor.tomorrow"]})
    return SimpleNamespace(module=module, hass=hass, entry=entry, states=states, registry=registry, clock=clock, track=track)


@pytest.fixture
def adapter(monkeypatch):
    return load_adapter(monkeypatch)


async def test_service_is_cached_local_only_once_per_installation(adapter):
    coordinator = adapter.module.DailyForecastCoordinator(adapter.hass, adapter.entry)
    assert coordinator.data["days"][0]["energy_kwh"] == 10
    assert coordinator.update_interval == timedelta(minutes=30)
    result = await coordinator._async_update_data()
    adapter.hass.services.async_call.assert_awaited_once_with("forecast_solar", "get_forecast", {"config_entry": "roof", "resolution": "raw"}, blocking=True, return_response=True)
    assert result["series_status"] == "available"
    assert result["series"][0]["points"][0]["energy_kwh"] == .05


@pytest.mark.parametrize("missing_service,error", [(True, None), (False, RuntimeError), (False, TimeoutError), (False, ValueError)])
async def test_old_ha_or_missing_service_keeps_valid_totals(adapter, missing_service, error):
    adapter.hass.services.has_service.return_value = not missing_service
    if error:
        adapter.hass.services.async_call.side_effect = error
    coordinator = adapter.module.DailyForecastCoordinator(adapter.hass, adapter.entry)
    result = await coordinator._async_update_data()
    assert result["days"][0]["energy_kwh"] == 10
    assert result["series"] == []
    assert result["series_status"] == "unavailable"


async def test_stale_data_not_refreshed_into_false_freshness(adapter):
    coordinator = adapter.module.DailyForecastCoordinator(adapter.hass, adapter.entry)
    await coordinator._async_update_data()
    adapter.clock.now += timedelta(hours=3)
    result = coordinator.snapshot()
    assert result["days"][0]["energy_kwh"] is None
    assert result["series"] == []
    await coordinator._async_update_data()
    assert adapter.hass.services.async_call.await_count == 1


async def test_input_listener_local_updates_and_unload(adapter):
    coordinator = adapter.module.DailyForecastCoordinator(adapter.hass, adapter.entry)
    await coordinator.async_start()
    adapter.states["sensor.today"] = state("20")
    coordinator._input_changed(SimpleNamespace(data={"entity_id": "sensor.today"}))
    assert coordinator.data["days"][0]["energy_kwh"] == 20
    adapter.hass.services.async_call.assert_not_awaited()
    await coordinator.async_stop()
    adapter.track.return_value.assert_called_once()
    coordinator.async_shutdown.assert_awaited_once()


def test_registry_removal_does_not_turn_partial_installation_into_total(adapter):
    adapter.registry.pop("sensor.tomorrow")
    result = adapter.module.DailyForecastCoordinator(adapter.hass, adapter.entry).data
    assert all(day["energy_kwh"] is None for day in result["days"])
    assert "unsupported_forecast_entity" in result["issues"]


def test_measurements_distinguish_signed_power_energy_and_unknown(adapter):
    adapter.entry.options.update({CONF_DAILY_GRID_POWER_ENTITY: ["sensor.grid"], CONF_DAILY_SOLAR_ENERGY_ENTITY: ["sensor.missing"]})
    result = adapter.module.DailyForecastCoordinator(adapter.hass, adapter.entry).data
    assert result["measurements"][0]["value"] == -1500
    assert result["measurements"][0]["kind"] == "measured"
    assert result["measurements"][1]["value"] is None
    assert result["solar_surplus_kwh"] is None


def test_input_validation_and_clearable_optional_lists(adapter):
    validate = adapter.module.validate_daily_inputs
    assert validate(adapter.hass, {}) == {}
    assert validate(adapter.hass, adapter.entry.options) == {}
    assert validate(adapter.hass, {CONF_DAILY_FORECAST_ENTITIES: ["sensor.today"]*2}) == {CONF_DAILY_FORECAST_ENTITIES: "daily_duplicate_forecast"}
    assert validate(adapter.hass, {CONF_DAILY_GRID_POWER_ENTITY: ["sensor.today"]}) == {CONF_DAILY_GRID_POWER_ENTITY: "daily_invalid_unit"}
    assert validate(adapter.hass, {CONF_DAILY_GRID_POWER_ENTITY: ["sensor.grid"]*2}) == {CONF_DAILY_GRID_POWER_ENTITY: "daily_single_entity"}
    assert CONF_DAILY_CONSUMPTION_ENTITIES in validate(adapter.hass, {CONF_DAILY_CONSUMPTION_ENTITIES: ["sensor.today"]*9})


async def test_options_opt_in_preserve_smart_settings_and_empty_forecast(monkeypatch):
    module = _load_config_flow_for_test(monkeypatch)
    adapter = load_adapter(monkeypatch)
    original = {CONF_DAILY_ADVISOR_ENABLED: True, "smart_flexible_load_power_w": 1500, "selected_supplier": "zonneplan"}
    flow = _smart_setup_flow(module, original)
    flow.config_entry = SimpleNamespace(data={}, options=original)
    flow.hass = adapter.hass
    form = await flow.async_step_smart_setup({"smart_setup_enabled": False})
    assert form["step_id"] == "daily_advisor"
    result = await flow.async_step_daily_advisor({CONF_DAILY_FORECAST_ENTITIES: []})
    assert result["type"] == "create_entry"
    assert result["data"]["smart_flexible_load_power_w"] == 1500
    assert result["data"]["selected_supplier"] == "zonneplan"
    assert result["data"][CONF_DAILY_ADVISOR_ENABLED] is True


async def test_disabling_daily_keeps_all_selections_and_skips_validation(monkeypatch):
    module = _load_config_flow_for_test(monkeypatch)
    options = {CONF_DAILY_ADVISOR_ENABLED: False, CONF_DAILY_FORECAST_ENTITIES: ["sensor.offline"]}
    flow = _smart_setup_flow(module, options)
    result = await flow.async_step_smart_setup({"smart_setup_enabled": False})
    assert result["type"] == "create_entry"
    assert result["data"][CONF_DAILY_FORECAST_ENTITIES] == ["sensor.offline"]


@pytest.mark.parametrize("key", [CONF_DAILY_ADVISOR_ENABLED, *DAILY_INPUT_KEYS])
def test_daily_changes_rebuild_entities_and_listeners(key):
    assert _requires_reload({}, {key: True if key == CONF_DAILY_ADVISOR_ENABLED else ["sensor.new"]})
    assert not _requires_reload({}, {})


def test_nl_en_and_source_translations_have_daily_controls():
    base = ROOT / "custom_components/nl_day_ahead_prices"
    for name in ("strings.json", "translations/en.json", "translations/nl.json"):
        content = json.loads((base/name).read_text())
        assert CONF_DAILY_ADVISOR_ENABLED in content["options"]["step"]["init"]["data"]
        assert set(DAILY_INPUT_KEYS) <= content["options"]["step"]["daily_advisor"]["data"].keys()
        assert "solar_forecast_today" in content["entity"]["sensor"]
        assert "daily_forecast_quality" in content["entity"]["sensor"]
        assert "get_daily_forecast" in content["services"]


def test_three_sensors_have_stable_ids_and_bounded_attributes(adapter, monkeypatch):
    class Entity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

    modules = {
        "homeassistant.components.sensor": {"SensorDeviceClass": SimpleNamespace(ENUM="enum", ENERGY="energy"), "SensorEntity": type("Sensor", (), {})},
        "homeassistant.const": {"EntityCategory": SimpleNamespace(DIAGNOSTIC="diagnostic"), "UnitOfEnergy": SimpleNamespace(KILO_WATT_HOUR="kWh")},
    }
    for name, attrs in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys.modules["homeassistant.helpers.update_coordinator"], "CoordinatorEntity", Entity, raising=False)
    name = "custom_components.nl_day_ahead_prices._daily_sensor_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "custom_components/nl_day_ahead_prices/daily_sensor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    coordinator = adapter.module.DailyForecastCoordinator(adapter.hass, adapter.entry)
    entities = module.daily_forecast_entities(coordinator, adapter.entry)
    assert [entity._attr_unique_id for entity in entities] == ["test_solar_forecast_today", "test_solar_forecast_tomorrow", "test_daily_forecast_quality"]
    assert [entity.native_value for entity in entities] == [10, 12, "partial"]
    for entity in entities:
        assert entity._attr_device_info["identifiers"] == {("nl_day_ahead_prices", "test")}
        assert len(entity.extra_state_attributes["days"]) == 3
        assert "series" not in entity.extra_state_attributes
        assert not hasattr(entity, "_attr_state_class")
    adapter.states.clear()
    coordinator._clock_tick(NOW)
    assert not entities[0].available
    assert entities[2].available
    assert entities[2].native_value == "unavailable"


@pytest.mark.parametrize("enabled", [False, True])
async def test_entry_setup_nonblocking_and_optional_unload(adapter, monkeypatch, enabled):
    from custom_components.nl_day_ahead_prices import async_setup_entry, async_unload_entry

    price = SimpleNamespace(async_start=AsyncMock(), async_stop=AsyncMock(), async_refresh=AsyncMock(), registry_updated=Mock())
    coordinator_module = ModuleType("custom_components.nl_day_ahead_prices.coordinator")
    coordinator_module.NLDayAheadPricesCoordinator = lambda hass, entry: price
    monkeypatch.setitem(sys.modules, coordinator_module.__name__, coordinator_module)
    delivery = SimpleNamespace(async_start=AsyncMock(), async_stop=AsyncMock())
    delivery_module = ModuleType("custom_components.nl_day_ahead_prices.daily_delivery")
    delivery_module.DailyAdvisorDelivery = Mock(return_value=delivery)
    monkeypatch.setitem(sys.modules, delivery_module.__name__, delivery_module)
    registry_module = ModuleType("custom_components.nl_day_ahead_prices.remote_registry")
    registry_module.async_get_manager = AsyncMock()
    registry_module.async_subscribe = Mock(return_value=Mock())
    monkeypatch.setitem(sys.modules, registry_module.__name__, registry_module)
    const = ModuleType("homeassistant.const")
    const.Platform = SimpleNamespace(SENSOR="sensor", BINARY_SENSOR="binary_sensor", NUMBER="number", SWITCH="switch")
    monkeypatch.setitem(sys.modules, const.__name__, const)
    monkeypatch.setattr(adapter.module.DailyForecastCoordinator, "async_refresh", AsyncMock(), raising=False)
    hass, entry = adapter.hass, adapter.entry
    entry.options[CONF_DAILY_ADVISOR_ENABLED] = enabled
    entry.async_on_unload = Mock()
    entry.add_update_listener = Mock()
    entry.async_create_background_task = Mock(side_effect=lambda hass, coro, name: coro.close())
    hass.async_create_task = Mock(side_effect=lambda coro: coro.close())
    hass.async_add_executor_job = AsyncMock()
    hass.data = {}
    hass.config_entries = SimpleNamespace(async_forward_entry_setups=AsyncMock(), async_unload_platforms=AsyncMock(return_value=True))
    assert await async_setup_entry(hass, entry)
    assert (price.daily_forecast is not None) == enabled
    assert entry.async_create_background_task.call_count == 2 * int(enabled)
    hass.services.async_call.assert_not_awaited()
    assert await async_unload_entry(hass, entry)
    price.async_stop.assert_awaited_once()
    if enabled:
        delivery.async_stop.assert_awaited_once()
        price.daily_forecast.async_shutdown.assert_awaited_once()


async def test_response_service_disabled_local_only_and_cannot_mutate_data(adapter, monkeypatch):
    from tests.test_service_lifecycle import FakeServices

    modules = {
        "voluptuous": {"Schema": lambda value: value, "Optional": lambda value, **kwargs: value, "In": lambda value: value},
        "homeassistant.core": {"HomeAssistant": object, "ServiceCall": object, "SupportsResponse": SimpleNamespace(ONLY="only")},
        "homeassistant.helpers.config_validation": {"boolean": bool},
        "custom_components.nl_day_ahead_prices.sensor": {"_energy_tax": Mock(), "_selected_supplier_profile": Mock(), "_vat": Mock()},
        "custom_components.nl_day_ahead_prices.services_v2": {"async_register_v2_services": Mock()},
    }
    for name, attrs in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    name = "custom_components.nl_day_ahead_prices._daily_services_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "custom_components/nl_day_ahead_prices/services.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hass = SimpleNamespace(services=FakeServices(), data={})
    module.async_register_services(hass)
    module.async_register_services(hass)
    handler, options = hass.services.registered[("nl_day_ahead_prices", "get_daily_forecast")]
    assert options["supports_response"] == "only"
    assert await handler(None) == {"enabled": False, "days": [], "series": []}
    daily = adapter.module.DailyForecastCoordinator(adapter.hass, adapter.entry)
    hass.data["nl_day_ahead_prices"] = {"test": SimpleNamespace(daily_forecast=daily)}
    result = await handler(None)
    assert result["enabled"]
    result["days"][0]["energy_kwh"] = 999
    assert daily.data["days"][0]["energy_kwh"] == 10
    adapter.hass.services.async_call.assert_not_awaited()
