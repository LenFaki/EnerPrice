"""Belgian wholesale setup, providers, DST, services and NL isolation."""

import importlib.util
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest
import yaml

from custom_components.nl_day_ahead_prices.const import DOMAIN
from custom_components.nl_day_ahead_prices.market import BE_PRICE_KEYS, validate_market
from custom_components.nl_day_ahead_prices.market_dashboard import generate_market_dashboard_yaml
from custom_components.nl_day_ahead_prices.models import PriceData, PriceEntry, ProviderResult
from custom_components.nl_day_ahead_prices.providers import (
    EnergyChartsProvider,
    EntsoeProvider,
    NordPoolProvider,
    ProviderError,
    async_fetch_with_fallback,
)
from tests.test_coordinator_morning import runtime as runtime
from tests.test_registry_ux import _load_config_flow_for_test, _load_services_v2_for_smart_defaults, _smart_setup_flow
from tests.test_sensor_attributes import day_prices, make_sensor
from tests.test_sensor_attributes import sensor_module as sensor_module
from tests.test_service_lifecycle import FakeServices


@pytest.mark.parametrize("options", [{"country": "DE"}, {"country": "BE", "currency": "USD"}])
def test_unsupported_market_never_silently_uses_nl(options):
    with pytest.raises(ValueError):
        validate_market(options)
    with pytest.raises(ValueError):
        NordPoolProvider(None, options["country"], options.get("currency", "EUR"))


@pytest.mark.parametrize("provider_type", [NordPoolProvider, EnergyChartsProvider, EntsoeProvider])
@pytest.mark.parametrize("day,hours", [(date(2026,3,29),23), (date(2026,10,25),25), (date(2026,11,24),24)])
@pytest.mark.parametrize("minutes", [15,60])
async def test_be_provider_request_and_complete_dst_day(provider_type, day, hours, minutes):
    """Synthetic provider-shaped fixtures include a deliberately different NL price."""
    zone = ZoneInfo("Europe/Brussels")
    start = datetime.combine(day, datetime.min.time(), zone).astimezone(timezone.utc)
    times = [start + timedelta(minutes=minutes*i) for i in range(hours*60//minutes)]
    prices = [-30 + i for i in range(len(times))]
    if provider_type is NordPoolProvider:
        payload = {"multiAreaEntries": [{"deliveryStart": t.isoformat(), "entryPerArea": {"BE": price,"NL":999}}
                                         for t,price in zip(times,prices,strict=True)]}
        provider = provider_type(None, "be")
        provider._request_json = request = AsyncMock(side_effect=[payload, {"multiAreaEntries": []}])
    elif provider_type is EnergyChartsProvider:
        payload = {"unix_seconds": [int(t.timestamp()) for t in times], "price":prices}
        provider = provider_type(None, "BE")
        provider._request_json = request = AsyncMock(side_effect=[payload, {"unix_seconds":[],"price":[]}])
    else:
        payload = (f'<Publication_MarketDocument><TimeSeries><Period><timeInterval><start>{start.isoformat()}</start>'
                   f'</timeInterval><resolution>PT{minutes}M</resolution>' + ''.join(
                   f'<Point><position>{i+1}</position><price.amount>{p}</price.amount></Point>' for i,p in enumerate(prices))
                   + '</Period></TimeSeries></Publication_MarketDocument>')
        provider = provider_type(None, "test-token", "BE")
        provider._request_text = request = AsyncMock(side_effect=[payload, '<Publication_MarketDocument/>'])
    result = await provider.async_fetch(day, day+timedelta(days=1))
    assert len(result.prices_today) == hours*60//minutes
    assert len({p.time.timestamp() for p in result.prices_today}) == len(times)
    assert result.prices_tomorrow == []
    assert result.prices_today[0].price == pytest.approx(-.03)
    assert result.raw_price_resolution == ("quarter_hour" if minutes==15 else "hourly")
    params = request.call_args_list[0].args[1]
    if provider_type is NordPoolProvider:
        assert params["deliveryArea"] == "BE" and params["currency"] == "EUR"
    elif provider_type is EnergyChartsProvider:
        assert params["bzn"] == "BE"
        assert (datetime.fromisoformat(params["end"]).timestamp()-datetime.fromisoformat(params["start"]).timestamp())/3600 == hours
    else:
        assert params["in_Domain"] == params["out_Domain"] == "10YBE----------2"
        assert params["periodStart"] == start.strftime("%Y%m%d%H%M")
        assert params["periodEnd"] == (start+timedelta(hours=hours)).strftime("%Y%m%d%H%M")


async def test_be_fallback_retains_bidding_zone_without_tomorrow():
    first, second = NordPoolProvider(None,"BE"), EnergyChartsProvider(None,"BE")
    first._request_json = AsyncMock(side_effect=ProviderError("unavailable"))
    instant = datetime(2026,11,23,23,tzinfo=timezone.utc)
    second._request_json = AsyncMock(side_effect=[{"unix_seconds":[instant.timestamp()], "price":[150]}, {"price":[]}])
    result, fallback, errors = await async_fetch_with_fallback([first,second],date(2026,11,24),date(2026,11,25))
    assert fallback and errors == {"nord_pool":"unavailable"}
    assert result.prices_today[0].price == .15
    assert all(call.args[1]["bzn"] == "BE" for call in second._request_json.call_args_list)


@pytest.mark.parametrize("extended", [False, True])
def test_be_prices_ignore_stored_nl_tax_supplier_and_smart_settings(sensor_module, extended, monkeypatch):
    sensor = make_sensor(sensor_module, day_prices("2026-09-08",15),extended)
    sensor.entry.options.update(country="BE",selected_supplier="powerpeers",vat=.21,energy_tax=.5,
                                smart_setup_enabled=True,daily_advisor_enabled=True)
    assert sensor.native_value == .1
    monkeypatch.setattr(sensor_module, "_cached_all_in_entries", Mock(side_effect=AssertionError("contract conversion")))
    monkeypatch.setattr(sensor_module, "_cached_sell_entries", Mock(side_effect=AssertionError("export revenue")))
    attrs = sensor.extra_state_attributes
    assert attrs["price_basis"] == "wholesale_ex_vat" and not attrs["taxes_and_supplier_fees_included"]
    assert len(attrs["prices_today"]) == 96 and attrs["prices_tomorrow"] == []
    assert not {"all_in_prices", "sell_prices_today", "supplier_monthly_fee", "vat", "energy_tax"} & attrs.keys()
    assert sensor_module._vat(sensor.entry) == sensor_module._energy_tax(sensor.entry) == 0


async def test_be_setup_exposes_only_supported_price_entities(sensor_module):
    sensor = make_sensor(sensor_module, day_prices("2026-09-08",60))
    sensor.entry.options.update(country="BE",smart_setup_enabled=True,daily_advisor_enabled=True)
    sensor.coordinator.daily_forecast = None
    entities = []
    await sensor_module.async_setup_entry(SimpleNamespace(data={DOMAIN:{"test":sensor.coordinator}}),sensor.entry,entities.extend)
    keys = {entity.entity_description.key for entity in entities}
    assert keys == BE_PRICE_KEYS
    assert {"current_market_price","average_price_today","price_trajectory","price_level"} <= keys
    for key in ("current_all_in_price","current_sell_price","selected_supplier","price_score","price_advisor"):
        assert key not in keys
        stale = sensor_module.NLDayAheadPriceSensor(sensor.coordinator,sensor.entry,next(d for d in sensor_module.SENSORS if d.key==key))
        assert stale.native_value is None


async def test_be_options_skip_nl_contract_steps_and_validate_currency(monkeypatch):
    module = _load_config_flow_for_test(monkeypatch)
    flow = _smart_setup_flow(module,{"country":"NL","selected_supplier":"powerpeers","vat":.21})
    flow._current_options = lambda: dict(flow._pending_options)
    result = await flow.async_step_init({"country":"BE","currency":"EUR"})
    assert result["step_id"] == "belgian_market"
    result = await flow.async_step_belgian_market({})
    assert result["type"] == "create_entry" and result["data"]["country"] == "BE"
    assert result["data"]["selected_supplier"] == "powerpeers"  # Preserved only for switching back to NL.
    assert module._validate_base_options({"country":"BE","currency":"USD"}) == {"currency":"belgium_eur_only"}
    assert module._validate_base_options({"country":"UK"}) == {"country":"unsupported_market"}


@pytest.mark.parametrize("source_country", [None,"NL","BE"])
async def test_be_cache_never_promotes_nl_prices(runtime, source_country):
    coordinator, _, _, now = runtime
    coordinator.entry.options.update(country="BE",price_resolution="auto")
    cached = {"local_date":now.date().isoformat(),"prices_today":[{"time":now.isoformat(),"price":.1}],
              "last_successful_update":now.isoformat()}
    if source_country:
        cached["country"] = source_country
    coordinator.store.async_load.return_value = cached
    result = await coordinator._async_load_cached()
    assert (result is not None) == (source_country=="BE")
    assert coordinator._effective_price_resolution("auto",now) == "quarter_hour"
    if result:
        await coordinator._async_store_cached(result)
        assert coordinator.store.async_save.call_args.args[0]["country"] == "BE"


async def test_be_coordinator_fallback_configuration(runtime):
    coordinator, _, _, _ = runtime
    coordinator.entry.options.update(country="BE", enable_entsoe=True, entsoe_api_token="test")
    providers = coordinator._providers()
    assert {p.key for p in providers} == {"nord_pool","energy_charts","entsoe"}
    assert all(p.country=="BE" for p in providers)


def service_runtime(monkeypatch, country="BE"):
    module = _load_services_v2_for_smart_defaults(monkeypatch)
    entry = SimpleNamespace(entry_id="test",data={},options={"country":country})
    now = module.dt_util.now()
    prices = [PriceEntry(now+timedelta(minutes=15*i), .1+i*.01) for i in range(96)]
    coordinator = SimpleNamespace(entry=entry,data=SimpleNamespace(result=SimpleNamespace(prices=prices)))
    hass = SimpleNamespace(services=FakeServices(),data={DOMAIN:{"test":coordinator}},config=SimpleNamespace(language="nl"))
    module.async_register_v2_services(hass)
    return module,hass,coordinator,now


@pytest.mark.parametrize("name", ["get_smart_energy_advice","find_best_export_window","find_battery_strategy"])
async def test_be_services_reject_contract_based_advice(monkeypatch,name):
    module,hass,coordinator,_ = service_runtime(monkeypatch)
    handler,_ = hass.services.registered[(DOMAIN,name)]
    result = await handler(SimpleNamespace(service=name,data={}))
    assert "error" in result and result["pricing_mode"] == "wholesale_only"
    module._selected_supplier_profile.assert_not_called()


async def test_be_planning_defaults_market_and_rejects_all_in(monkeypatch):
    module,hass,coordinator,now = service_runtime(monkeypatch)
    handler,_ = hass.services.registered[(DOMAIN,"find_best_appliance_window")]
    args = {"appliance_name":"washer","duration_minutes":60,"deadline":now+timedelta(hours=6),"energy_kwh":1}
    result = await handler(SimpleNamespace(service="find_best_appliance_window",data=args))
    assert "error" not in result
    assert result["price_basis"] == "wholesale_ex_vat" and result["estimated_cost"] == pytest.approx(.115)
    module._selected_supplier_profile.assert_not_called()
    for basis in ("all_in","sell"):
        result = await handler(SimpleNamespace(service="find_best_appliance_window",data={**args,"price_type":basis}))
        assert "error" in result
        with pytest.raises(ValueError):
            module._prices(coordinator,basis,True)


async def test_be_dashboard_service_uses_resolved_ids_without_nl_cards(monkeypatch):
    module,hass,_,_ = service_runtime(monkeypatch)
    module._dashboard_entity_ids = Mock(return_value={"current_market_price":"sensor.renamed_be","current_all_in_price":"sensor.stale_nl"})
    handler,_ = hass.services.registered[(DOMAIN,"generate_dashboard_yaml")]
    result = await handler(SimpleNamespace(service="generate_dashboard_yaml",data={"language":"auto","include_all_in":True}))
    assert "sensor.renamed_be" in result["yaml"] and "sensor.stale_nl" not in result["yaml"]
    assert "exclusief btw" in result["yaml"]
    assert yaml.safe_load(result["yaml"])["views"][0]["cards"]
    empty = json.loads(generate_market_dashboard_yaml({}))
    assert len(empty["views"][0]["cards"]) == 1


async def test_be_diagnostics_do_not_claim_nl_tariffs(sensor_module, monkeypatch):
    sensor = make_sensor(sensor_module,day_prices("2026-09-08",60))
    sensor.entry.options.update(country="BE",vat=.21,energy_tax=.5,selected_supplier="powerpeers")
    sensor.coordinator.fetch_diagnostics = {}
    monkeypatch.setitem(sys.modules,"custom_components.nl_day_ahead_prices.sensor",sensor_module)
    path=Path(__file__).parents[1]/"custom_components/nl_day_ahead_prices/diagnostics.py"
    spec=importlib.util.spec_from_file_location("custom_components.nl_day_ahead_prices._be_diagnostics",path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result=await module.async_get_config_entry_diagnostics(SimpleNamespace(data={DOMAIN:{"test":sensor.coordinator}}),sensor.entry)
    assert result["selected_planning_options"]["price_type"] == "market"
    assert result["price_basis"] == "wholesale_ex_vat"
    assert "supplier_tariff" not in result and "supplier_registry" not in result
    assert "vat" not in result["options"] and "energy_tax" not in result["options"]


async def test_be_automation_uses_actual_market_period_entity(monkeypatch):
    module, hass, _, _ = service_runtime(monkeypatch)
    module._dashboard_entity_ids = Mock(return_value={"best_price_period": "binary_sensor.be_renamed_period"})
    handler, _ = hass.services.registered[(DOMAIN, "generate_automation_yaml")]
    args = {"automation_type": "appliance_best_window", "target_entity": "switch.washer"}
    result = await handler(SimpleNamespace(service="generate_automation_yaml", data=args))
    assert "entity_id: binary_sensor.be_renamed_period" in result["yaml"]
    assert "BE wholesale only" in result["yaml"]
    result = await handler(SimpleNamespace(service="generate_automation_yaml", data={**args, "automation_type": "battery_charge_discharge"}))
    assert "error" in result


async def test_be_export_chart_uses_market_default_and_rejects_all_in(monkeypatch):
    module, hass, coordinator, now = service_runtime(monkeypatch)
    # Load the chart services against the same runtime instead of the helper stub.
    path = Path(__file__).parents[1] / "custom_components/nl_day_ahead_prices/services.py"
    spec = importlib.util.spec_from_file_location("custom_components.nl_day_ahead_prices._be_chart_services", path)
    charts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(charts)
    result = ProviderResult("nord_pool", coordinator.data.result.prices, [])
    coordinator.data = PriceData(result, False, now)
    coordinator.runtime_options = {}
    monkeypatch.setitem(sys.modules, "custom_components.nl_day_ahead_prices.services_v2", module)
    charts.async_register_services(hass)
    handler, _ = hass.services.registered[(DOMAIN, "export_chart_data")]
    args = {"include_today": True, "include_tomorrow": True, "resolution": "quarter_hour",
            "include_best_periods": False, "include_peak_periods": False}
    response = await handler(SimpleNamespace(data=args))
    assert response["prices"][0]["price"] == .1
    assert response["price_basis"] == "wholesale_ex_vat"
    response = await handler(SimpleNamespace(data={**args, "price_type": "all_in"}))
    assert "error" in response
    module._selected_supplier_profile.assert_not_called()
