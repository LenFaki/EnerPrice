"""Public sensor attribute regressions with lightweight HA entity doubles."""

import importlib.util
import json
import sys
from dataclasses import make_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from custom_components.nl_day_ahead_prices.cache import get_cached_prices_for_date
from custom_components.nl_day_ahead_prices.const import CONF_EXTENDED_ATTRIBUTES, RUNTIME_DEFAULTS
from custom_components.nl_day_ahead_prices.models import PriceData, PriceEntry, ProviderResult
from custom_components.nl_day_ahead_prices.remote_registry import parse_remote_registry
from custom_components.nl_day_ahead_prices.supplier_registry import activate_remote_registry


@pytest.fixture
def sensor_module(monkeypatch):
    class CoordinatorEntity:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, coordinator):
            self.coordinator = coordinator

    description = make_dataclass("SensorEntityDescription", [
        (key, object, None) for key in (
            "key", "translation_key", "native_unit_of_measurement", "device_class",
            "options", "state_class", "suggested_display_precision", "entity_registry_enabled_default", "entity_category",
        )
    ], frozen=True, kw_only=True)
    modules = {
        "homeassistant": {},
        "homeassistant.components": {},
        "homeassistant.components.sensor": {
            "SensorDeviceClass": SimpleNamespace(ENUM="enum", TIMESTAMP="timestamp"),
            "SensorStateClass": SimpleNamespace(MEASUREMENT="measurement"),
            "SensorEntity": type("SensorEntity", (), {}), "SensorEntityDescription": description,
        },
        "homeassistant.config_entries": {"ConfigEntry": object},
        "homeassistant.const": {"UnitOfEnergy": SimpleNamespace(KILO_WATT_HOUR="kWh"),
                                "EntityCategory": SimpleNamespace(DIAGNOSTIC="diagnostic")},
        "homeassistant.core": {"HomeAssistant": object, "callback": lambda func: func},
        "homeassistant.helpers": {},
        "homeassistant.helpers.entity_platform": {"AddEntitiesCallback": object},
        "homeassistant.helpers.event": {"async_track_state_change_event": lambda *args, **kwargs: (lambda: None)},
        "homeassistant.helpers.update_coordinator": {"CoordinatorEntity": CoordinatorEntity},
        "homeassistant.util": {"dt": SimpleNamespace(now=lambda: datetime(2026, 9, 8, 10, tzinfo=timezone.utc))},
        "custom_components.nl_day_ahead_prices.coordinator": {"NLDayAheadPricesCoordinator": object},
    }
    for name, attributes in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    name = "custom_components.nl_day_ahead_prices._sensor_test"
    path = Path(__file__).parents[1] / "custom_components/nl_day_ahead_prices/sensor.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def make_sensor(module, prices, extended=False, cached=False):
    result = ProviderResult("cache" if cached else "nord_pool", prices, [],
                            effective_price_resolution="quarter_hour" if len(prices) > 25 else "hourly")
    data = PriceData(result, cached, prices[0].time, from_cache=cached,
                     data_completeness="cache_promoted_tomorrow" if cached else "today_only")
    coordinator = SimpleNamespace(data=data, runtime_options={**RUNTIME_DEFAULTS, CONF_EXTENDED_ATTRIBUTES: extended})
    entry = SimpleNamespace(entry_id="test", data={}, options={})
    description = module.NLPriceSensorDescription(key="average_price_today", value_fn=module._average_today)
    return module.NLDayAheadPriceSensor(coordinator, entry, description)


def test_remote_activation_preserves_all_dashboard_arrays(sensor_module):
    sensor = make_sensor(sensor_module, day_prices("2026-09-08", 15))
    sensor.entry.options["selected_supplier"] = "anwb_energie"
    sensor.coordinator.data.result.prices_tomorrow = day_prices("2026-09-09", 15)
    before = sensor.extra_state_attributes
    candidate = json.loads((Path(__file__).parents[1] / "registry/supplier_tariffs.json").read_text())
    candidate["revision"] = 2
    candidate["suppliers"]["anwb_energie"]["tariffs"][0]["purchase_fee_import"] = 0.04
    activate_remote_registry(parse_remote_registry(candidate))
    try:
        after = sensor.extra_state_attributes
        for key in ("prices", "prices_today", "prices_tomorrow"):
            assert after[key] == before[key]
        for key in ("all_in_prices", "all_in_prices_today", "all_in_prices_tomorrow"):
            assert len(after[key]) == len(before[key])
            for old, new in zip(before[key], after[key], strict=True):
                assert set(new) == {"time", "price"}
                assert new["time"] == old["time"]
                assert new["price"] - old["price"] == pytest.approx(0.022)
    finally:
        activate_remote_registry(None)


def day_prices(day, minutes):
    zone = ZoneInfo("Europe/Amsterdam")
    start = datetime.fromisoformat(day).replace(tzinfo=zone).astimezone(timezone.utc)
    end = (datetime.fromisoformat(day).replace(tzinfo=zone) + timedelta(days=1)).astimezone(timezone.utc)
    prices = []
    while start < end:
        prices.append(PriceEntry(start.astimezone(zone), 0.1))
        start += timedelta(minutes=minutes)
    return prices


@pytest.mark.parametrize("extended", [False, True])
def test_core_and_optional_attributes(sensor_module, extended, monkeypatch):
    sensor = make_sensor(sensor_module, day_prices("2026-09-08", 60), extended)
    if not extended:
        monkeypatch.setattr(sensor_module, "_cheapest_block_attributes", Mock(side_effect=AssertionError("unneeded analysis")))
    attrs = sensor.extra_state_attributes
    required = {
        "prices", "prices_today", "prices_tomorrow", "all_in_prices", "all_in_prices_today", "all_in_prices_tomorrow",
        "raw_prices", "raw_prices_today", "raw_prices_tomorrow", "raw_price_resolution",
        "price_resolution", "requested_price_resolution", "effective_price_resolution", "resolution_converted",
        "provider", "provider_name", "fallback_used", "cache_used", "data_completeness",
        "last_successful_update", "selected_supplier", "selected_supplier_name",
    }
    assert required <= attrs.keys()
    assert ("supplier_profile" in attrs) == extended
    assert ("trend" in attrs) == extended
    assert attrs["prices_tomorrow"] == attrs["all_in_prices_tomorrow"] == []
    assert attrs["all_in_prices"] == attrs["all_in_prices_today"]
    for key in ("prices_today", "all_in_prices_today", "all_in_prices"):
        assert isinstance(attrs[key], list)
        for item in attrs[key]:
            assert set(item) == {"time", "price"}
            assert isinstance(item["price"], (int, float))
            assert datetime.fromisoformat(item["time"]).tzinfo is not None
    assert attrs["all_in_prices_today"][0]["price"] > attrs["prices_today"][0]["price"]
    assert attrs["all_in_prices"][0]["price"] > attrs["prices"][0]["price"]
    assert sensor.suggested_object_id == "nl_day_ahead_prices_average_price_today"


@pytest.mark.parametrize("day,hours", [("2026-09-08", 24), ("2026-03-29", 23), ("2026-10-25", 25)])
@pytest.mark.parametrize("minutes", [15, 60])
def test_chart_intervals_and_dst(sensor_module, day, hours, minutes):
    attrs = make_sensor(sensor_module, day_prices(day, minutes)).extra_state_attributes
    assert len(attrs["all_in_prices"]) == len(attrs["prices"])
    assert [item["time"] for item in attrs["all_in_prices"]] == [item["time"] for item in attrs["prices"]]
    for key in ("prices_today", "all_in_prices_today", "all_in_prices"):
        values = attrs[key]
        assert len(values) == hours * 60 // minutes
        timestamps = [datetime.fromisoformat(item["time"]).timestamp() for item in values]
        assert all(b - a == minutes * 60 for a, b in zip(timestamps, timestamps[1:], strict=False))


def test_promoted_cache_chart_attributes(sensor_module):
    prices = day_prices("2026-09-08", 60)
    cached = {"local_date": "2026-09-07", "prices_tomorrow": [item.as_attribute() for item in prices]}
    restored = get_cached_prices_for_date(cached, prices[0].time.date())
    entries = [PriceEntry(datetime.fromisoformat(item["time"]), item["price"]) for item in restored["prices"]]
    attrs = make_sensor(sensor_module, entries, cached=True).extra_state_attributes
    assert restored["status"] == attrs["data_completeness"] == "cache_promoted_tomorrow"
    assert attrs["cache_used"]
    assert len(attrs["prices_today"]) == len(attrs["all_in_prices_today"]) == 24
    assert attrs["all_in_prices"] == attrs["all_in_prices_today"]
    assert len(attrs["all_in_prices"]) == len(attrs["prices"])


@pytest.mark.parametrize("minutes", [15, 60])
@pytest.mark.parametrize("extended", [False, True])
def test_combined_all_in_today_and_tomorrow(sensor_module, minutes, extended):
    sensor = make_sensor(sensor_module, day_prices("2026-09-08", minutes), extended)
    sensor.coordinator.data.result.prices_tomorrow = day_prices("2026-09-09", minutes)
    attrs = sensor.extra_state_attributes
    combined = attrs["all_in_prices"]
    assert combined == attrs["all_in_prices_today"] + attrs["all_in_prices_tomorrow"]
    assert len(combined) == len(attrs["prices"]) == 48 * 60 // minutes
    assert [item["time"] for item in combined] == [item["time"] for item in attrs["prices"]]
    timestamps = [datetime.fromisoformat(item["time"]).timestamp() for item in combined]
    assert all(b - a == minutes * 60 for a, b in zip(timestamps, timestamps[1:], strict=False))


@pytest.mark.parametrize("extended", [False, True])
def test_registry_used_by_existing_sensor_and_custom_options(sensor_module, extended):
    sensor = make_sensor(sensor_module, day_prices("2026-09-08", 60), extended)
    sensor.entry.options = {"selected_supplier": "tibber"}
    attrs = sensor.extra_state_attributes
    assert attrs["supplier_purchase_fee"] == 0.018
    assert attrs["supplier_tariff_valid_from"] == "2026-09-01"
    assert attrs["supplier_registry_source"] == "bundled"
    assert attrs["supplier_settlement_resolution"] == "quarter_hour"
    assert attrs["all_in_prices"][0]["price"] == pytest.approx(0.1 * 1.21 + 0.1108 + 0.018)
    sensor.entry.options = {"selected_supplier": "custom", "custom_purchase_fee_electricity": 0.05,
                            "custom_purchase_fee_includes_vat": False}
    custom = sensor.extra_state_attributes
    assert custom["supplier_tariff_status"] == "custom"
    assert custom["supplier_purchase_fee"] == pytest.approx(0.0605)
    assert custom["supplier_tariff_last_verified"] is None
    assert custom["all_in_prices"][0]["price"] == pytest.approx(0.1 * 1.21 + 0.1108 + 0.0605)


@pytest.mark.parametrize("minutes", [15, 60])
@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize("custom", [False, True])
def test_tibber_tomorrow_fee_changes_before_midnight(sensor_module, monkeypatch, minutes, extended, custom):
    zone = ZoneInfo("Europe/Amsterdam")
    now = datetime(2026, 8, 31, 15, tzinfo=zone)
    monkeypatch.setattr(sensor_module.dt_util, "now", lambda: now)
    sensor = make_sensor(sensor_module, day_prices("2026-08-31", minutes), extended)
    sensor.coordinator.data.result.prices_tomorrow = day_prices("2026-09-01", minutes)
    sensor.entry.options = {"selected_supplier": "tibber"}
    if custom:
        sensor.entry.options = {"selected_supplier": "custom", "custom_purchase_fee_electricity": 0.05,
                                "custom_purchase_fee_includes_vat": True}
    attrs = sensor.extra_state_attributes
    today_fee, tomorrow_fee = (0.05, 0.05) if custom else (0.0248, 0.018)
    base = 0.1 * 1.21 + 0.1108
    assert attrs["supplier_purchase_fee"] == today_fee
    assert all(item["price"] == pytest.approx(base + today_fee) for item in attrs["all_in_prices_today"])
    assert all(item["price"] == pytest.approx(base + tomorrow_fee) for item in attrs["all_in_prices_tomorrow"])
    assert attrs["all_in_prices"] == attrs["all_in_prices_today"] + attrs["all_in_prices_tomorrow"]
    tomorrow_start = attrs["all_in_prices_tomorrow"][0]
    assert tomorrow_start["time"] == "2026-09-01T00:00:00+02:00"
    for item in attrs["all_in_prices"]:
        assert set(item) == {"time", "price"}
        assert isinstance(item["price"], (int, float))
        assert datetime.fromisoformat(item["time"]).tzinfo is not None
    assert sensor_module._average_tomorrow(sensor.coordinator.data, now, sensor.entry) == pytest.approx(base + tomorrow_fee)
    before_midnight = datetime(2026, 8, 31, 23, 59, tzinfo=zone)
    assert sensor_module._next_hour_all_in(sensor.coordinator.data, before_midnight, sensor.entry) == pytest.approx(base + tomorrow_fee)


AMSTERDAM_ZONE = ZoneInfo("Europe/Amsterdam")
BOUNDARY_PUBLISHED = datetime(2026, 8, 31, 15, tzinfo=AMSTERDAM_ZONE)
BEFORE_BOUNDARY = datetime(2026, 8, 31, 23, 30, tzinfo=AMSTERDAM_ZONE)
AFTER_BOUNDARY = datetime(2026, 9, 1, 0, 30, tzinfo=AMSTERDAM_ZONE)
CUSTOM_SELL_OPTIONS = {"selected_supplier": "custom", "custom_sell_fee_electricity": 0.03,
                       "custom_sell_fee_includes_vat": True}


def boundary_runtime(sensor_module, monkeypatch, minutes, options):
    """Today/tomorrow prices straddling the tariff boundary, plus the real Daily Advisor context."""
    from tests.test_daily_forecast import load_adapter

    clock = SimpleNamespace(now=BOUNDARY_PUBLISHED)
    monkeypatch.setattr(sensor_module.dt_util, "now", lambda: clock.now)
    price_sensor = make_sensor(sensor_module, day_prices("2026-08-31", minutes), extended=True)
    coordinator, entry = price_sensor.coordinator, price_sensor.entry
    coordinator.data.result.prices_tomorrow = day_prices("2026-09-01", minutes)
    coordinator.hass = SimpleNamespace(config=SimpleNamespace(language="en"))
    coordinator.cached_analysis = lambda key, factory: factory()
    coordinator.entry = entry
    entry.options = dict(options)
    daily_advisor = load_adapter(monkeypatch).module
    # The Daily Advisor reads the supplier profile and VAT from the sensor module under test.
    monkeypatch.setitem(sys.modules, "custom_components.nl_day_ahead_prices.sensor", sensor_module)

    def entity(key):
        description = next(item for item in sensor_module.SENSORS if item.key == key)
        return sensor_module.NLDayAheadPriceSensor(coordinator, entry, description)

    def advisor_exports():
        context = daily_advisor.advisor_context(
            SimpleNamespace(daily_forecast=SimpleNamespace(snapshot=dict), entry=entry, data=coordinator.data)
        )
        return context["export_prices"]

    return SimpleNamespace(clock=clock, coordinator=coordinator, entry=entry, price_sensor=price_sensor,
                           entity=entity, advisor_exports=advisor_exports)


def current_sell_outputs(runtime, when):
    """Every public current sell price at one moment, keyed by output."""
    from custom_components.nl_day_ahead_prices.models import current_price

    runtime.clock.now = when
    attrs = runtime.price_sensor.extra_state_attributes
    series = [PriceEntry(datetime.fromisoformat(item["time"]), item["price"])
              for item in attrs["sell_prices_today"] + attrs["sell_prices_tomorrow"]]
    return {
        "current_sell_price_sensor": runtime.entity("current_sell_price").native_value,
        "export_advisor": runtime.entity("export_advisor").extra_state_attributes["current_sell_price"],
        "sell_price_attribute": attrs["sell_price"],
        "sell_prices_series": current_price(series, when),
        "daily_advisor_export_prices": current_price(runtime.advisor_exports(), when),
    }


@pytest.mark.parametrize("minutes", [15, 60])
@pytest.mark.parametrize("custom", [False, True])
def test_sell_outputs_agree_with_export_advisor_across_tariff_boundary(sensor_module, monkeypatch, minutes, custom):
    """All sell outputs use the tariff of each interval's own date, like the export advisors."""
    runtime = boundary_runtime(sensor_module, monkeypatch, minutes, CUSTOM_SELL_OPTIONS if custom else {"selected_supplier": "tibber"})
    market = 0.1 * 1.21
    # Custom fees never follow the registry.
    today_sell, tomorrow_sell = (market - 0.03, market - 0.03) if custom else (market - 0.0248, market - 0.018)

    # Tomorrow's prices are published while "now" is still before the boundary.
    attrs = runtime.price_sensor.extra_state_attributes
    assert all(item["price"] == pytest.approx(today_sell) for item in attrs["sell_prices_today"])
    assert all(item["price"] == pytest.approx(tomorrow_sell) for item in attrs["sell_prices_tomorrow"])
    assert attrs["sell_prices_tomorrow"][0]["time"] == "2026-09-01T00:00:00+02:00"
    assert attrs["sell_prices_today"] + attrs["sell_prices_tomorrow"] == [
        item.as_attribute() for item in runtime.advisor_exports()
    ]
    best_export = runtime.entity("best_export_period").extra_state_attributes["price"]
    assert best_export == pytest.approx(max(today_sell, tomorrow_sell))

    for when, expected in ((BEFORE_BOUNDARY, today_sell), (AFTER_BOUNDARY, tomorrow_sell)):
        outputs = current_sell_outputs(runtime, when)
        assert outputs == {key: pytest.approx(expected) for key in outputs}, when


def test_sell_outputs_follow_market_vat_flag_across_tariff_boundary(sensor_module, monkeypatch):
    """A tariff that stops paying VAT on the export market price applies from its own date only."""
    candidate = json.loads((Path(__file__).parents[1] / "registry/supplier_tariffs.json").read_text())
    candidate["revision"] = 2
    september = next(item for item in candidate["suppliers"]["tibber"]["tariffs"] if item["valid_from"] == "2026-09-01")
    september["export_market_vat_included"] = False
    activate_remote_registry(parse_remote_registry(candidate))
    try:
        runtime = boundary_runtime(sensor_module, monkeypatch, 60, {"selected_supplier": "tibber"})
        attrs = runtime.price_sensor.extra_state_attributes
        assert all(item["price"] == pytest.approx(0.1 * 1.21 - 0.0248) for item in attrs["sell_prices_today"])
        assert all(item["price"] == pytest.approx(0.1 - 0.018) for item in attrs["sell_prices_tomorrow"])
        for when, expected in ((BEFORE_BOUNDARY, 0.1 * 1.21 - 0.0248), (AFTER_BOUNDARY, 0.1 - 0.018)):
            outputs = current_sell_outputs(runtime, when)
            assert outputs == {key: pytest.approx(expected) for key in outputs}, when
    finally:
        activate_remote_registry(None)


@pytest.mark.parametrize("include_fee", [True, False])
async def test_export_window_service_uses_interval_date_tariff(sensor_module, monkeypatch, include_fee):
    """find_best_export_window agrees with the Daily Advisor export prices across the boundary."""
    from custom_components.nl_day_ahead_prices.const import DOMAIN
    from tests.test_registry_ux import _load_services_v2_for_smart_defaults
    from tests.test_service_lifecycle import FakeServices

    runtime = boundary_runtime(sensor_module, monkeypatch, 60, {"selected_supplier": "tibber"})
    services = _load_services_v2_for_smart_defaults(monkeypatch)
    # The loader installs a sensor stub; services and the Daily Advisor must share the real supplier lookup.
    monkeypatch.setitem(sys.modules, "custom_components.nl_day_ahead_prices.sensor", sensor_module)
    monkeypatch.setattr(services, "_selected_supplier_profile", sensor_module._selected_supplier_profile)
    monkeypatch.setattr(services, "_vat", sensor_module._vat)
    hass = SimpleNamespace(services=FakeServices(), data={DOMAIN: {runtime.entry.entry_id: runtime.coordinator}},
                           config=SimpleNamespace(language="en"))
    services.async_register_v2_services(hass)
    handler, _ = hass.services.registered[(DOMAIN, "find_best_export_window")]

    prices = services._prices(runtime.coordinator, "sell", include_fee)
    result = await handler(SimpleNamespace(service="find_best_export_window", data={
        "price_type": "sell", "include_supplier_sell_fee": include_fee, "duration_minutes": 60,
    }))
    if include_fee:
        assert prices == runtime.advisor_exports()
        # Tomorrow's lower export fee makes it the best window.
        assert result["best_export_start"].startswith("2026-09-01T00:00")
        assert result["average_sell_price"] == pytest.approx(0.1 * 1.21 - 0.018)
    else:
        assert all(item.price == pytest.approx(0.1 * 1.21) for item in prices)
        assert result["average_sell_price"] == pytest.approx(0.1 * 1.21)


def test_average_all_in_sensor_keeps_statistics_unit(sensor_module):
    description = next(item for item in sensor_module.SENSORS if item.key == "average_all_in_price_today")
    assert description.native_unit_of_measurement == "EUR/kWh"
    assert description.state_class == "measurement"
    sensor = make_sensor(sensor_module, day_prices("2026-09-08", 15))
    sensor.entity_description = description
    assert sensor.native_value is not None
    assert sensor.entity_description.native_unit_of_measurement == "EUR/kWh"


def test_large_chart_attributes_are_unrecorded_but_remain_public(sensor_module):
    sensor = make_sensor(sensor_module, day_prices("2026-09-08", 15))
    sensor.coordinator.data.result.prices_tomorrow = day_prices("2026-09-09", 15)
    attrs = sensor.extra_state_attributes
    large = {
        "prices", "prices_today", "prices_tomorrow",
        "all_in_prices", "all_in_prices_today", "all_in_prices_tomorrow",
        "raw_prices", "raw_prices_today", "raw_prices_tomorrow",
    }
    assert large <= attrs.keys()
    assert large <= sensor_module.NLDayAheadPriceSensor._unrecorded_attributes
    # Dashboards still receive the exact public arrays; only recorder history omits them.
    assert attrs["all_in_prices"] == attrs["all_in_prices_today"] + attrs["all_in_prices_tomorrow"]


def test_cheapest_block_attributes_are_unrecorded(sensor_module):
    # Each block repeats its price entries; together with the chart arrays they
    # push the state past the recorder's attribute size limit, which then drops
    # every attribute for that state.
    attrs = make_sensor(sensor_module, day_prices("2026-09-08", 15), extended=True).extra_state_attributes
    cheapest = {key for key in attrs if key.startswith("cheapest_")}
    assert len(cheapest) == 7
    assert cheapest <= sensor_module.NLDayAheadPriceSensor._unrecorded_attributes


def test_trajectory_reuses_all_in_and_trend_within_coordinator_cycle(sensor_module, monkeypatch):
    """Trajectory state and attributes must share expensive analysis work."""
    sensor = make_sensor(sensor_module, day_prices("2026-09-08", 15), extended=True)
    description = next(item for item in sensor_module.SENSORS if item.key == "price_trajectory")
    sensor.entity_description = description

    cache = {}
    def cached_analysis(key, factory):
        if key not in cache:
            cache[key] = factory()
        return cache[key]
    sensor.coordinator.cached_analysis = cached_analysis

    original_all_in = sensor_module._all_in_entries
    original_trend = sensor_module.trend_for_prices
    all_in_calls = 0
    trend_calls = 0

    def counted_all_in(*args, **kwargs):
        nonlocal all_in_calls
        all_in_calls += 1
        return original_all_in(*args, **kwargs)

    def counted_trend(*args, **kwargs):
        nonlocal trend_calls
        trend_calls += 1
        return original_trend(*args, **kwargs)

    monkeypatch.setattr(sensor_module, "_all_in_entries", counted_all_in)
    monkeypatch.setattr(sensor_module, "trend_for_prices", counted_trend)

    assert sensor.native_value is not None
    attrs = sensor.extra_state_attributes
    assert attrs["trend"] is not None
    assert all_in_calls == 1
    assert trend_calls == 1


def test_sell_series_is_built_once_per_coordinator_cycle(sensor_module, monkeypatch):
    """The sell price state, chart attributes and export advisor share one sell series."""
    price_sensor = make_sensor(sensor_module, day_prices("2026-09-08", 15), extended=True)
    coordinator, entry = price_sensor.coordinator, price_sensor.entry
    coordinator.hass = SimpleNamespace(config=SimpleNamespace(language="en"))
    cache = {}
    def cached_analysis(key, factory):
        if key not in cache:
            cache[key] = factory()
        return cache[key]
    coordinator.cached_analysis = cached_analysis

    def entity(key):
        description = next(item for item in sensor_module.SENSORS if item.key == key)
        return sensor_module.NLDayAheadPriceSensor(coordinator, entry, description)

    original = sensor_module._sell_entries
    calls = 0
    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(sensor_module, "_sell_entries", counted)

    sell_sensor, export_advisor = entity("current_sell_price"), entity("export_advisor")
    for _ in range(3):
        state = sell_sensor.native_value
        assert state is not None
    attrs = price_sensor.extra_state_attributes
    assert export_advisor.extra_state_attributes["current_sell_price"] == pytest.approx(state)
    assert attrs["sell_price"] == pytest.approx(state)
    assert calls == 1

    # Clearing the cache (new data, interval boundary, option change) rebuilds the series.
    cache.clear()
    assert sell_sensor.native_value == state
    assert calls == 2


def test_rating_does_not_calculate_trend(sensor_module, monkeypatch):
    """Rating sensors should not pay for unrelated trend analysis."""
    sensor = make_sensor(sensor_module, day_prices("2026-09-08", 15))
    description = next(item for item in sensor_module.SENSORS if item.key == "price_rating")
    sensor.entity_description = description
    monkeypatch.setattr(
        sensor_module,
        "trend_for_prices",
        Mock(side_effect=AssertionError("rating must not calculate trend")),
    )
    assert sensor.native_value in {"low", "normal", "high"}


def test_smart_advisor_reads_completed_snapshot_without_recalculating(sensor_module, monkeypatch):
    """HA state and attributes read the same completed background result."""
    coordinator = SimpleNamespace(data=object(), daily_forecast=None)
    entry = SimpleNamespace(entry_id="test", data={}, options={})
    sensor = sensor_module.NLSmartEnergyAdvisorSensor(coordinator, entry)
    sensor._result = {"state": "cheap_grid", "recommendation": "result 1"}
    monkeypatch.setattr(
        sensor, "_build_calculation",
        Mock(side_effect=AssertionError("state properties must not calculate advice")),
    )
    assert sensor.native_value == "cheap_grid"
    assert sensor.extra_state_attributes["recommendation"] == "result 1"

    # A state write must not invalidate the completed snapshot or recompute.
    monkeypatch.setattr(
        type(sensor).__mro__[1], "async_write_ha_state", lambda self: None, raising=False
    )
    sensor.async_write_ha_state()
    assert sensor.native_value == "cheap_grid"
    assert sensor.extra_state_attributes["recommendation"] == "result 1"
    sensor._build_calculation.assert_not_called()
