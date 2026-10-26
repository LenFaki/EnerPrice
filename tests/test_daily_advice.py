"""PR2 regressions for the existing advisor's calendar-day forecast extension."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.nl_day_ahead_prices.calculations import export_entries_for_supplier
from custom_components.nl_day_ahead_prices.daily_cache import advice_time_bucket
from custom_components.nl_day_ahead_prices.dashboard import generate_dashboard_yaml
from custom_components.nl_day_ahead_prices.models import PriceEntry
from custom_components.nl_day_ahead_prices.smart_energy import (
    _solar_load_energy,
    build_smart_energy_advice,
)
from custom_components.nl_day_ahead_prices.solar_forecast import AMSTERDAM
from custom_components.nl_day_ahead_prices.supplier_profiles import normalize_supplier_profile

NOW = datetime(2026, 10, 12, 10, tzinfo=AMSTERDAM)


def prices_for_day(day=NOW, minutes=60, price=.3):
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    return [PriceEntry(start + timedelta(minutes=i*minutes), price) for i in range(int((end-start).total_seconds()/60/minutes))]


def context(prices, *, now=NOW, minutes=60, solar_w=2000, grid_w=-1500, production=2000):
    stamps = [item.time for item in prices] + [prices[-1].time + timedelta(minutes=minutes)] if prices else []
    return {
        "resolution_minutes": minutes, "duration_minutes": 60,
        "export_prices": [PriceEntry(item.time, .05) for item in prices],
        "forecast": {
            "quality": "partial", "series_status": "available", "installation_count": 1,
            "days": [{"date": (now.date()+timedelta(days=i)).isoformat(), "energy_kwh": 10 if i < 2 else None, "status": "available" if i < 2 else "missing"} for i in range(3)],
            "series": [{"installation": "roof", "points": [{"time": stamp.isoformat(), "power_w": production} for stamp in stamps]}],
            "measurements": [
                {"role": "grid_power", "value": grid_w, "kind": "measured", "last_updated": now.isoformat()},
                {"role": "solar_power", "value": solar_w, "kind": "measured", "last_updated": now.isoformat()},
            ],
        },
    }


def daily(prices, ctx=None, *, now=NOW, power=1000, language="en"):
    return build_smart_energy_advice(electricity_price=None, future_prices=prices, now=now,
                                    flexible_load_power_w=power, language=language,
                                    daily_context=ctx if ctx is not None else context(prices, now=now))["daily_advisor"]


def test_future_advice_chooses_earliest_qualifying_interval_from_unsorted_prices():
    """One-pass selection must preserve the old chronological choice."""
    now = NOW.astimezone(timezone.utc)
    entries = [
        PriceEntry(now + timedelta(hours=4), .10),
        PriceEntry(now - timedelta(hours=1), .01),
        PriceEntry(now + timedelta(hours=2), .19),
        PriceEntry(now + timedelta(hours=1), .30),
        PriceEntry(now + timedelta(hours=3), .18),
    ]
    result = build_smart_energy_advice(
        electricity_price=.25, future_prices=entries, now=now,
    )
    assert result["next_better_time"] == now + timedelta(hours=2)
    assert result["next_better_price"] == .19
    assert result["minutes_until_better"] == 120


def test_future_advice_returns_no_window_when_no_price_qualifies():
    now = NOW.astimezone(timezone.utc)
    result = build_smart_energy_advice(
        electricity_price=.25,
        future_prices=[PriceEntry(now + timedelta(hours=1), .24)],
        now=now,
    )
    assert result["next_better_time"] is None
    assert result["minutes_until_better"] is None


def test_daily_known_interval_count_and_partial_status_are_preserved():
    prices = prices_for_day()
    prices.pop(4)
    result = daily(prices, context(prices))
    today = result["days"][0]
    assert today["price_status"] == "partial"
    assert today["known_intervals"] == 23
    assert today["expected_intervals"] == 24


def test_legacy_advice_unchanged_without_daily_context():
    args = {"electricity_price": .22, "now": NOW, "gas_price_per_m3": 1.3, "grid_power_w": -700, "flexible_load_power_w": 1500}
    before = build_smart_energy_advice(**args)
    after = build_smart_energy_advice(**args, daily_context=context([]))
    assert "daily_advisor" not in before
    assert {key: value for key, value in after.items() if key != "daily_advisor"} == before


@pytest.mark.parametrize("minutes", [15, 60])
def test_negative_price_window_and_explicit_financial_reference(minutes):
    prices = prices_for_day(minutes=minutes)
    prices = [PriceEntry(item.time, -.2 if item.time.astimezone(AMSTERDAM).hour == 13 else .3) for item in prices]
    result = daily(prices, context(prices, minutes=minutes))
    best = result["best_window"]
    assert datetime.fromisoformat(best["start"]).astimezone(AMSTERDAM).hour == 13
    assert best["duration_minutes"] == 60
    assert best["estimated_cost_eur"] == -.2
    assert best["estimated_impact_eur"] == .5
    assert best["reference"] == "earliest_remaining_grid_window"
    assert result["realized_savings_eur"] is None


def test_solar_scenario_includes_foregone_export_revenue():
    prices = prices_for_day()
    result = daily(prices)
    option = next(item for item in result["advice"] if item["model"] == "solar_scenario")
    assert option["estimated_cost_eur"] == .05  # Not zero: export opportunity cost.
    assert option["estimated_impact_eur"] == .25
    assert option["confidence"] == "low"
    assert option["reference"] == "same_window_grid_only"
    assert option["assumed_household_load_w"] == 500
    assert result["measured_export_w"] == 1500


@pytest.mark.parametrize("export", [-.05, .5])
def test_negative_export_fee_or_high_export_value_preserves_signed_impact(export):
    prices = prices_for_day()
    ctx = context(prices)
    ctx["export_prices"] = [PriceEntry(item.time, export) for item in prices]
    option = next(item for item in daily(prices, ctx)["advice"] if item["model"] == "solar_scenario")
    assert option["estimated_cost_eur"] == export
    assert option["estimated_impact_eur"] == pytest.approx(.3-export)


def test_solar_production_is_not_surplus_when_house_uses_it():
    prices = prices_for_day()
    result = daily(prices, context(prices, grid_w=1000, solar_w=2000, production=2000))
    assert result["assumed_household_load_w"] == 3000
    assert not any(item["model"] == "solar_scenario" for item in result["advice"])


@pytest.mark.parametrize("problem", ["no_grid", "no_solar", "stale", "not_simultaneous", "partial_series", "missing_export", "duplicate_roof"])
def test_incomplete_inputs_never_turn_production_into_free_energy(problem):
    prices = prices_for_day()
    ctx = context(prices)
    forecast = ctx["forecast"]
    if problem == "no_grid":
        forecast["measurements"] = forecast["measurements"][1:]
    elif problem == "no_solar":
        forecast["measurements"] = forecast["measurements"][:1]
    elif problem == "stale":
        for row in forecast["measurements"]:
            row["last_updated"] = (NOW-timedelta(minutes=20)).isoformat()
    elif problem == "not_simultaneous":
        forecast["measurements"][0]["last_updated"] = (NOW-timedelta(minutes=6)).isoformat()
    elif problem == "partial_series":
        forecast["series_status"] = "partial"
    elif problem == "missing_export":
        ctx["export_prices"] = []
    else:
        forecast["series"] *= 2
        forecast["installation_count"] = 2
    result = daily(prices, ctx)
    assert result["best_window"] is not None
    assert all(item["model"] == "grid_only" for item in result["advice"])



@pytest.mark.parametrize("lag_seconds", [0, 120, 299, 300])
def test_fresh_asynchronous_power_snapshots_enable_conditional_solar(lag_seconds):
    prices = prices_for_day()
    ctx = context(prices)
    ctx["forecast"]["measurements"][0]["last_updated"] = (NOW-timedelta(seconds=lag_seconds)).isoformat()
    result = daily(prices, ctx)
    assert result["solar_scenario_available"] is True
    assert result["assumed_household_load_w"] == 500
    assert any(item["model"] == "solar_scenario" for item in result["advice"])


@pytest.mark.parametrize("lag_seconds", [301, 900, 1200])
def test_old_power_snapshots_never_enable_conditional_solar(lag_seconds):
    prices = prices_for_day()
    ctx = context(prices)
    ctx["forecast"]["measurements"][0]["last_updated"] = (NOW-timedelta(seconds=lag_seconds)).isoformat()
    result = daily(prices, ctx)
    assert result["solar_scenario_available"] is False
    assert "power_measurements_not_synchronized" in result["solar_diagnostics"]
    assert all(item["model"] == "grid_only" for item in result["advice"])


def test_future_prices_unknown_even_with_solar_forecast():
    result = daily(prices_for_day())
    assert [day["price_status"] for day in result["days"]] == ["known", "unknown", "unknown"]
    assert result["days"][1]["solar_energy_kwh"] == 10
    assert result["days"][1]["window"] is None
    assert result["days"][2]["window"] is None
    assert all(row["price"] is None for row in result["chart"] if datetime.fromisoformat(row["time"]).astimezone(AMSTERDAM).date() > NOW.date())


def test_no_prices_retains_three_days_without_advice_or_cost():
    result = daily([])
    assert len(result["days"]) == 3
    assert result["best_window"] is None
    assert result["advice"] == []
    assert result["state"] == "unavailable"


def test_gaps_never_extend_a_price_or_produce_false_cheapest_day():
    prices = [item for item in prices_for_day() if item.time.astimezone(AMSTERDAM).hour != 15]
    result = daily(prices)
    assert result["days"][0]["price_status"] == "partial"
    assert result["best_window"] is None
    assert result["advice"] == []


def test_no_power_keeps_window_but_cost_unknown():
    result = daily(prices_for_day(), power=None)
    assert result["best_window"] is not None
    assert result["best_window"]["estimated_cost_eur"] is None
    assert result["best_window"]["estimated_impact_eur"] is None


@pytest.mark.parametrize("day,hours", [(datetime(2026, 3, 29, tzinfo=AMSTERDAM), 23), (datetime(2026, 10, 25, tzinfo=AMSTERDAM), 25), (datetime(2026, 10, 12, tzinfo=AMSTERDAM), 24)])
@pytest.mark.parametrize("minutes", [15, 60])
def test_three_calendar_days_dst_and_chart_bounds(day, hours, minutes):
    prices = prices_for_day(day, minutes)
    result = daily(prices, context(prices, now=day, minutes=minutes), now=day)
    assert result["days"][0]["expected_intervals"] == hours*60//minutes
    assert result["days"][0]["price_status"] == "known"
    assert len(result["chart"]) <= 300
    assert len({row["time"] for row in result["chart"]}) == len(result["chart"])
    assert len(result["advice"]) <= 3


def test_fall_back_repeated_hour_is_not_deduplicated():
    day = datetime(2026, 10, 25, tzinfo=AMSTERDAM)
    prices = prices_for_day(day)
    prices = [PriceEntry(item.time, -.5 if item.time.astimezone(AMSTERDAM).hour == 2 and item.time.astimezone(AMSTERDAM).fold else .3) for item in prices]
    result = daily(prices, context(prices, now=day), now=day)
    assert datetime.fromisoformat(result["best_window"]["start"]).astimezone(AMSTERDAM).fold == 1
    assert result["best_window"]["estimated_cost_eur"] == -.5


def test_last_quarter_hour_does_not_become_an_hour():
    now = NOW.replace(hour=23, minute=45)
    prices = prices_for_day(now, 15)
    ctx = context(prices, now=now, minutes=15)
    ctx["duration_minutes"] = 15
    result = daily(prices, ctx, now=now)
    assert result["best_window"]["duration_minutes"] == 15
    assert result["best_window"]["energy_kwh"] == .25
    assert result["best_window"]["estimated_cost_eur"] == .075


def test_long_session_and_rounding_are_explicit():
    prices = prices_for_day()
    ctx = context(prices)
    ctx["duration_minutes"] = 75
    assert daily(prices, ctx)["best_window"]["duration_minutes"] == 120
    ctx["duration_minutes"] = 1440
    assert daily(prices, ctx)["best_window"] is None  # Cannot finish today.


def test_surplus_integral_clips_at_baseline_and_appliance_power():
    # Solar ramps 0..3000 W over an hour; household 1000 W, appliance 1000 W.
    assert _solar_load_energy([[(0, 0), (3600, 3000)]], 0, 3600, 1000, 1000) == pytest.approx(.5)
    assert _solar_load_energy([[(0, 0), (7200, 3000)]], 0, 3600, 1000, 1000) is None


def test_multiple_roofs_are_combined_before_surplus_clipping():
    assert _solar_load_energy([[(0, 600), (3600, 600)], [(0, 600), (3600, 600)]], 0, 3600, 1000, 1000) == pytest.approx(.2)


def test_export_conversion_respects_custom_fees_vat_and_effective_dates(monkeypatch):
    import custom_components.nl_day_ahead_prices.calculations as calculations

    profile = normalize_supplier_profile({"key": "custom", "name": "Custom", "purchase_fee_export": .02, "sell_fee_includes_vat": False, "feed_in_fee": .01, "imbalance_fee": .005})
    prices = [PriceEntry(NOW, .1), PriceEntry(NOW+timedelta(days=1), -.1)]
    result = export_entries_for_supplier(prices, profile, .21)
    assert result[0].price == pytest.approx(.121 - .0242 - .01 - .005)
    assert result[1].price == pytest.approx(-.121 - .0242 - .01 - .005)
    from dataclasses import replace

    profile = replace(profile, key="tibber", registry_source="bundled")
    next_profile = replace(profile, purchase_fee_export=.04)
    lookup = Mock(side_effect=lambda key, day, **kwargs: next_profile if day > NOW.date() else profile)
    monkeypatch.setattr(calculations, "get_supplier_tariff", lookup)
    result = export_entries_for_supplier(prices, profile, .21)
    assert result[1].price == pytest.approx(-.121 - .0484 - .01 - .005)
    assert lookup.call_count == 2


@pytest.mark.parametrize("language,word", [("en", "Lowest average"), ("nl", "Laagste gemiddelde")])
def test_daily_advice_localized(language, word):
    assert word in daily(prices_for_day(), language=language)["advice"][0]["reason"]


@pytest.mark.parametrize("layout", ["compact", "full", "energy_advisor"])
@pytest.mark.parametrize("language", ["en", "nl"])
def test_dashboard_daily_section_resolves_ids_and_builtin_fallback(layout, language):
    ids = {"daily_energy_advice": "sensor.renamed_daily", "solar_forecast_today": "sensor.renamed_solar", "best_consumption_window": "sensor.renamed_window"}
    result = generate_dashboard_yaml(layout, language=language, entity_ids=ids, include_daily_advisor=True, use_apexcharts=False)
    assert "sensor.renamed_daily" in result
    assert "entity: sensor.renamed_solar" in result
    assert "entity: sensor.renamed_window" in result
    assert "custom:" not in result
    assert "price_status" in result
    assert "[:3]" in result
    assert "realized_savings_eur" not in result


def test_missing_entities_are_not_guessed_and_default_dashboard_unchanged():
    assert "daily_energy_advice" not in generate_dashboard_yaml()
    missing = generate_dashboard_yaml(include_daily_advisor=True, entity_ids={})
    assert "Enable Daily Advisor" in missing
    assert "sensor.daily_energy_advice" not in missing
    assert generate_dashboard_yaml() == generate_dashboard_yaml(include_daily_advisor=False, use_apexcharts=True)


def test_daily_chart_uses_actual_bounded_attributes_and_preserves_unknowns():
    result = generate_dashboard_yaml(include_daily_advisor=True, entity_ids={"daily_energy_advice": "sensor.actual_daily"})
    assert "entity: sensor.actual_daily" in result
    assert "entity.attributes.chart ?? []" in result
    assert "p.solar_kw" in result
    assert "p.price" in result
    assert "p.price || 0" not in result


def test_inputs_are_not_mutated():
    prices = prices_for_day()
    ctx = context(prices)
    before = deepcopy(ctx)
    daily(prices, ctx)
    assert ctx == before


def test_mixed_solar_grid_window_is_not_discarded():
    prices = prices_for_day()
    ctx = context(prices)
    ctx["duration_minutes"] = 120
    # Isolated production peak; every two-hour window includes grid-only time.
    for point in ctx["forecast"]["series"][0]["points"]:
        point["power_w"] = 2000 if datetime.fromisoformat(point["time"]).astimezone(AMSTERDAM).hour == 12 else 0
    result = daily(prices, ctx)
    option = next(item for item in result["advice"] if item["model"] == "solar_scenario")
    assert option["duration_minutes"] == 120
    assert .1 < option["estimated_cost_eur"] < .6


@pytest.mark.parametrize("layout,language,digest", [
    ("compact", "en", "5cd4a8ede9ecae41695d7817a087f8f2089984289a29566a0e10db520ce4668b"),
    ("compact", "nl", "c591b584cb85b9d8b6da4fccb48899a9171c630ea6101092ac736171b63e66a3"),
    ("full", "en", "136dc3b84506607f634cee87af5336c89c45abcbc9b3f4b5c493da817f97a281"),
    ("full", "nl", "3a40ed9bef574e99563cbadeec97de9d09bb76abab57415cecf77a1f7626a2d1"),
    ("energy_advisor", "en", "136dc3b84506607f634cee87af5336c89c45abcbc9b3f4b5c493da817f97a281"),
    ("energy_advisor", "nl", "3a40ed9bef574e99563cbadeec97de9d09bb76abab57415cecf77a1f7626a2d1"),
])
def test_default_dashboard_byte_compatibility_with_pr1(layout, language, digest):
    assert sha256(generate_dashboard_yaml(layout, language=language).encode()).hexdigest() == digest


@pytest.mark.parametrize("language", ["nl", "en"])
@pytest.mark.parametrize("apexcharts", [False, True])
def test_generated_yaml_and_jinja_are_valid_and_render_unknown_prices(language, apexcharts):
    from jinja2 import Environment, StrictUndefined
    from yaml import safe_load

    result = daily(prices_for_day(), language=language)
    document = safe_load(generate_dashboard_yaml("compact", language=language, include_price_advisor=False,
                                                include_daily_advisor=True, use_apexcharts=apexcharts,
                                                entity_ids={"daily_energy_advice": "sensor.daily"}))
    cards = document["views"][0]["sections"][-1]["cards"]
    markdown = next(card["content"] for card in cards if card["type"] == "markdown")
    env = Environment(undefined=StrictUndefined)
    env.globals.update(state_attr=lambda entity, attr: result.get(attr), as_timestamp=lambda value: datetime.fromisoformat(value).timestamp())
    env.filters["timestamp_custom"] = lambda value, fmt: datetime.fromtimestamp(value, AMSTERDAM).strftime(fmt)
    rendered = env.from_string(markdown).render()
    assert "onbekend" in rendered if language == "nl" else "unknown" in rendered
    assert "0.25" in rendered


@pytest.fixture
def advice_runtime(monkeypatch):
    import sys

    from custom_components.nl_day_ahead_prices.calculations import all_in_entries_for_supplier
    from custom_components.nl_day_ahead_prices.const import CONF_FLEXIBLE_LOAD_POWER_W, DOMAIN
    from tests.test_daily_forecast import load_adapter
    from tests.test_registry_ux import _load_services_v2_for_smart_defaults
    from tests.test_service_lifecycle import FakeServices

    services = _load_services_v2_for_smart_defaults(monkeypatch)
    adapter = load_adapter(monkeypatch)
    adapter.clock.now = NOW
    monkeypatch.setattr(services.dt_util, "now", lambda: NOW)
    profile = normalize_supplier_profile({"key": "custom", "name": "Custom", "purchase_fee_import": .02, "purchase_fee_includes_vat": True})
    services._selected_supplier_profile.return_value = profile
    services._energy_tax.return_value = .1
    services._vat.return_value = .21
    prices = prices_for_day()
    all_in = all_in_entries_for_supplier(prices, .1, profile, .21)
    sensor = sys.modules["custom_components.nl_day_ahead_prices.sensor"]
    sensor._cached_all_in_entries = Mock(return_value=all_in)
    adapter.entry.options[CONF_FLEXIBLE_LOAD_POWER_W] = 1000
    adapter.hass.config = SimpleNamespace(language="nl")
    forecast = adapter.module.DailyForecastCoordinator(adapter.hass, adapter.entry)
    coordinator = SimpleNamespace(entry=adapter.entry, daily_forecast=forecast, hass=adapter.hass,
                                  data=SimpleNamespace(result=SimpleNamespace(prices=prices, effective_price_resolution="hourly")), analysis_cache={})
    adapter.hass.services = FakeServices()
    adapter.hass.data = {DOMAIN: {adapter.entry.entry_id: coordinator}}
    services.async_register_v2_services(adapter.hass)
    return SimpleNamespace(adapter=adapter, services=services, coordinator=coordinator, domain=DOMAIN)


async def test_existing_smart_service_includes_daily_context_and_explicit_power(advice_runtime):
    runtime = advice_runtime
    handler, _ = runtime.adapter.hass.services.registered[(runtime.domain, "get_smart_energy_advice")]
    result = await handler(SimpleNamespace(service="get_smart_energy_advice", data={"flexible_load_power_w": 2000, "language": "nl"}))
    best = result["daily_advisor"]["best_window"]
    assert best["estimated_cost_eur"] == .966  # 2 kWh * (.3 * 1.21 + .1 + .02).
    assert best["energy_kwh"] == 2
    assert "Laagste" in best["reason"]


async def test_no_market_prices_keep_daily_outlook_available(advice_runtime):
    runtime = advice_runtime
    runtime.coordinator.data = None
    handler, _ = runtime.adapter.hass.services.registered[(runtime.domain, "get_smart_energy_advice")]
    result = await handler(SimpleNamespace(service="get_smart_energy_advice", data={}))
    assert len(result["daily_advisor"]["days"]) == 3
    assert result["daily_advisor"]["advice"] == []
    runtime.coordinator.daily_forecast = None
    assert await handler(SimpleNamespace(service="get_smart_energy_advice", data={})) == {"error": "Price data is not available"}


def test_shared_daily_result_cache_invalidates_on_options_and_forecast(advice_runtime):
    runtime = advice_runtime
    calculate = runtime.adapter.module.daily_advice
    first = calculate(runtime.coordinator)
    assert calculate(runtime.coordinator) is first
    runtime.coordinator.entry.options["daily_duration_minutes"] = 120
    second = calculate(runtime.coordinator)
    assert second is not first
    assert second["best_window"]["duration_minutes"] == 120
    runtime.coordinator.daily_forecast.data = dict(runtime.coordinator.daily_forecast.data)
    assert calculate(runtime.coordinator) is not second
    assert set(runtime.coordinator.analysis_cache) == {"daily_advice"}


async def test_dashboard_service_resolves_daily_ids_and_flags(advice_runtime):
    runtime = advice_runtime
    entity = SimpleNamespace(platform=runtime.domain, disabled_by=None, unique_id="test_daily_energy_advice", entity_id="sensor.renamed")
    runtime.services.er.async_entries_for_config_entry.return_value = [entity]
    runtime.adapter.states["sensor.renamed"] = SimpleNamespace(state="ready")
    handler, _ = runtime.adapter.hass.services.registered[(runtime.domain, "generate_dashboard_yaml")]
    result = await handler(SimpleNamespace(service="generate_dashboard_yaml", data={"include_daily_advisor": True, "use_apexcharts": False}))
    assert "sensor.renamed" in result["yaml"]
    assert "custom:" not in result["yaml"]
    assert "Dagelijks energieadvies" in result["yaml"]


async def test_smart_advisor_subscribes_to_forecast_without_legacy_inputs(monkeypatch):
    from tests.test_sensor_attributes import sensor_module

    module = sensor_module.__wrapped__(monkeypatch)
    coordinator = SimpleNamespace(daily_forecast=SimpleNamespace(async_add_listener=Mock(return_value=Mock())))
    sensor = module.NLSmartEnergyAdvisorSensor(coordinator, SimpleNamespace(entry_id="entry", data={}, options={}))
    parent = type(sensor).__mro__[1]
    monkeypatch.setattr(parent, "async_added_to_hass", AsyncMock(), raising=False)
    sensor.async_on_remove = Mock()
    sensor.async_write_ha_state = Mock()
    await sensor.async_added_to_hass()
    coordinator.daily_forecast.async_add_listener.assert_called_once_with(sensor.async_write_ha_state)
    sensor.async_on_remove.assert_called_once_with(coordinator.daily_forecast.async_add_listener.return_value)


def test_daily_advice_cache_hit_skips_utc_price_conversion(monkeypatch):
    """Reading daily advice twice in one minute must not rebuild UTC prices."""
    import sys
    from types import ModuleType

    from custom_components.nl_day_ahead_prices import models, smart_energy
    from tests.test_daily_forecast import load_adapter

    # Load the real advisor through the project's existing HA-free test adapter.
    module = load_adapter(monkeypatch).module
    prices = prices_for_day()
    sensor_stub = ModuleType("custom_components.nl_day_ahead_prices.sensor")
    sensor_stub._cached_all_in_entries = lambda *_: prices
    monkeypatch.setitem(sys.modules, sensor_stub.__name__, sensor_stub)
    monkeypatch.setattr(module, "advisor_context", lambda *_: {})
    monkeypatch.setattr(smart_energy, "build_smart_energy_advice",
                        Mock(return_value={"daily_advisor": {"state": "ready"}}))

    coordinator = SimpleNamespace(
        entry=SimpleNamespace(data={}, options={}),
        hass=SimpleNamespace(config=SimpleNamespace(language="en")),
        data=object(),
        daily_forecast=SimpleNamespace(data=object()),
        analysis_cache={},
    )
    module.dt_util.utcnow = lambda: NOW.astimezone(timezone.utc)
    first = module.daily_advice(coordinator)
    assert first["state"] == "ready"

    def fail_on_conversion(*args, **kwargs):
        raise AssertionError("cache hit should not rebuild PriceEntry objects")

    monkeypatch.setattr(models, "PriceEntry", fail_on_conversion)
    assert module.daily_advice(coordinator) is first


@pytest.mark.parametrize("elapsed,expected_warning", [(.31, True), (.004, False)])
def test_daily_advice_profiling_warns_only_on_slow_cache_misses(advice_runtime, monkeypatch, elapsed, expected_warning):
    """Diagnostic timings must not change advice or warn on cache hits."""
    module = advice_runtime.adapter.module
    coordinator = advice_runtime.coordinator
    # started, conversion end, context end, calculation end, total end.
    ticks = iter([0.0, .001, .002, elapsed - .001, elapsed])
    monkeypatch.setattr(module, "perf_counter", lambda: next(ticks))
    # Test outer recomputation timings independently of nested context timings.
    monkeypatch.setattr(module, "advisor_context", lambda *_: {})
    warning = Mock()
    monkeypatch.setattr(module._LOGGER, "warning", warning)

    result = module.daily_advice(coordinator)
    assert module.daily_advice(coordinator) is result
    assert warning.called is expected_warning
    if expected_warning:
        assert warning.call_count == 1
        assert "recomputation took" in warning.call_args.args[0]
        assert warning.call_args.args[1] == pytest.approx(elapsed * 1000)


@pytest.mark.parametrize("snapshot_seconds,export_seconds,expected_warning", [
    (.28, .02, True), (.01, .01, False),
])
def test_daily_advisor_context_profiles_snapshot_and_export(advice_runtime, monkeypatch,
                                                             snapshot_seconds, export_seconds, expected_warning):
    """Only slow contexts log, and each stage is timed separately."""
    module = advice_runtime.adapter.module
    coordinator = advice_runtime.coordinator
    ticks = iter([0.0, snapshot_seconds, snapshot_seconds + export_seconds])
    monkeypatch.setattr(module, "perf_counter", lambda: next(ticks))
    warning = Mock()
    monkeypatch.setattr(module._LOGGER, "warning", warning)

    result = module.advisor_context(coordinator)

    assert result["forecast"] is not None
    assert len(result["export_prices"]) == len(coordinator.data.result.prices)
    assert warning.called is expected_warning
    if expected_warning:
        args = warning.call_args.args
        assert "context took" in args[0]
        assert args[1] == pytest.approx((snapshot_seconds + export_seconds) * 1000)
        assert args[2] == pytest.approx(snapshot_seconds * 1000)
        assert args[3] == pytest.approx(export_seconds * 1000)


def test_daily_context_profiles_export_conversion(advice_runtime, monkeypatch):
    """Export conversion must be inside the GC profiling scope."""
    from contextlib import contextmanager

    module = advice_runtime.adapter.module
    coordinator = advice_runtime.coordinator
    entered = []

    @contextmanager
    def fake_profile(label, logger):
        entered.append(label)
        try:
            yield
        finally:
            entered.append("done")

    monkeypatch.setattr(module, "profile_advisor_gc", fake_profile)
    result = module.advisor_context(coordinator)
    assert result["export_prices"]
    assert entered == ["Daily Advisor export prices", "done"]


def test_daily_solar_interpolation_reuses_forecast_boundaries(monkeypatch):
    """Repeated chart/solar timestamps should only interpolate once per advice."""
    import custom_components.nl_day_ahead_prices.smart_energy as smart_energy

    prices = prices_for_day(minutes=15)
    ctx = context(prices, minutes=15)
    original = smart_energy._power_at
    calls = []

    def tracked(series, stamp):
        calls.append(stamp)
        return original(series, stamp)

    monkeypatch.setattr(smart_energy, "_power_at", tracked)
    result = daily(prices, ctx)
    assert result["solar_scenario_available"]
    assert len(calls) == len(set(calls))
    assert len(calls) > 0


def test_solar_integral_cached_and_uncached_are_identical():
    """Caching cannot change interpolation, clipping, or missing-gap handling."""
    from functools import cache

    from custom_components.nl_day_ahead_prices.smart_energy import _power_at

    series = [[(0, 0), (900, 2000), (1800, 600), (2700, 2400), (3600, 0)]]
    cached = cache(lambda stamp: _power_at(series, stamp))
    for baseline, power in [(0, 1000), (500, 1000), (1000, 2000)]:
        assert _solar_load_energy(series, 0, 3600, baseline, power) == _solar_load_energy(
            series, 0, 3600, baseline, power, power_at=cached
        )
    assert _solar_load_energy(series, 3600, 4500, 500, 1000, power_at=cached) is None


def test_daily_advice_time_bucket_limits_recomputation_to_five_minutes():
    start = datetime(2026, 10, 25, 13, 0, tzinfo=timezone.utc)
    assert advice_time_bucket(start) == advice_time_bucket(start + timedelta(minutes=4, seconds=59))
    assert advice_time_bucket(start) != advice_time_bucket(start + timedelta(minutes=5))
