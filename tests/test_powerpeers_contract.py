"""Customer-confirmed Powerpeers contracts and dated export rules."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from custom_components.nl_day_ahead_prices import _requires_reload
from custom_components.nl_day_ahead_prices.calculations import all_in_entries_for_supplier, export_entries_for_supplier
from custom_components.nl_day_ahead_prices.contract_pricing import (
    CONF_POWERPEERS_CONFIRMED,
    POWERPEERS_FLAGS,
    POWERPEERS_NUMBERS,
    powerpeers_export_price,
    powerpeers_profile,
)
from custom_components.nl_day_ahead_prices.models import PriceEntry
from tests.test_coordinator_morning import runtime as runtime
from tests.test_registry_ux import _load_config_flow_for_test, _smart_setup_flow
from tests.test_sensor_attributes import sensor_module as sensor_module


def contract(**overrides):
    return {CONF_POWERPEERS_CONFIRMED: True,
            "custom_monthly_fee_electricity": 6.75,
            "custom_purchase_fee_electricity": .01,
            "custom_sell_fee_electricity": .01,
            "custom_purchase_fee_includes_vat": True,
            "custom_sell_fee_includes_vat": True,
            "custom_sell_market_price_includes_vat": True, **overrides}


@pytest.mark.parametrize("missing", [CONF_POWERPEERS_CONFIRMED, *POWERPEERS_NUMBERS, *POWERPEERS_FLAGS])
def test_missing_contract_setting_cannot_silently_use_defaults(missing):
    options = contract()
    del options[missing]
    with pytest.raises(ValueError):
        powerpeers_profile(options)


@pytest.mark.parametrize("invalid", [-.01, float("nan"), float("inf"), "0.01", True])
@pytest.mark.parametrize("key", POWERPEERS_NUMBERS)
def test_invalid_contract_rejected(key, invalid):
    with pytest.raises(ValueError):
        powerpeers_profile(contract(**{key: invalid}))


def test_customer_contract_isolated_from_registry_and_other_entries(sensor_module):
    first = SimpleNamespace(data={}, options={"selected_supplier": "powerpeers", **contract()})
    second = SimpleNamespace(data={}, options={"selected_supplier": "powerpeers", **contract(custom_purchase_fee_electricity=.02)})
    a, b = map(sensor_module._selected_supplier_profile, (first, second))
    prices = [PriceEntry(datetime(2026, 11, 24, tzinfo=timezone.utc), .1)]
    assert a.registry_source == "custom" and a.key == "powerpeers"
    assert a.monthly_fee_electricity == 6.75 and a.price_resolution == "quarter_hour"
    assert all_in_entries_for_supplier(prices, .110848, a, .21)[0].price == pytest.approx(.241848)
    assert all_in_entries_for_supplier(prices, .110848, b, .21)[0].price == pytest.approx(.251848)


@pytest.mark.parametrize("market", [-.1, 0, .1])
@pytest.mark.parametrize("day", [date(2026,12,31), date(2027,1,1), date(2029,12,31), date(2030,1,1)])
@pytest.mark.parametrize("include_fee", [False, True])
def test_documented_export_formula_and_year_boundaries(market, day, include_fee):
    profile = powerpeers_profile(contract())
    base = market * 1.21 if day.year < 2027 else max(market, .5 * (market + .01 / 1.21)) if day.year < 2030 else market
    assert powerpeers_export_price(market, profile, .21, day, include_fee=include_fee) == pytest.approx(base - (.01 if include_fee else 0))


def test_export_date_is_local_and_explicit_worker_tariffs_preserve_contract():
    profile = powerpeers_profile(contract(custom_purchase_fee_electricity=.02,
                                         custom_purchase_fee_includes_vat=False,
                                         custom_sell_fee_includes_vat=False))
    prices = [PriceEntry(datetime(2026, 12, 31, 22, 45, tzinfo=timezone.utc), -.1),
              PriceEntry(datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc), -.1)]
    tariffs = {date(2026,12,31):profile, date(2027,1,1):profile}
    assert [p.price for p in export_entries_for_supplier(prices, profile, .21, tariffs_by_date=tariffs)] == pytest.approx([-.121-.0121, -.04-.0121])
    assert [p.price for p in export_entries_for_supplier(prices, profile, .21, include_fee=False)] == pytest.approx([-.121, -.04])
    # The monthly floor must not become a fabricated per-interval zero floor.
    assert export_entries_for_supplier(prices, profile, .21)[1].price < 0


async def test_powerpeers_flow_requires_confirmation_and_never_assumes_monthly_fee(monkeypatch):
    module = _load_config_flow_for_test(monkeypatch)
    monkeypatch.setattr(module.vol, "Required", module.vol.Optional, raising=False)
    flow = _smart_setup_flow(module, {"selected_supplier": "powerpeers"})
    result = await flow.async_step_powerpeers_contract()
    monthly = next(key for key in result["data_schema"] if key.key == "custom_monthly_fee_electricity")
    vat = next(key for key in result["data_schema"] if key.key == "custom_sell_market_price_includes_vat")
    assert monthly.default is None and vat.default is None
    invalid = await flow.async_step_powerpeers_contract({**contract(), CONF_POWERPEERS_CONFIRMED: False})
    assert invalid["errors"] == {"base": "invalid_powerpeers_contract"}
    result = await flow.async_step_powerpeers_contract(contract())
    assert result["step_id"] == "smart_setup"
    assert flow._pending_options[CONF_POWERPEERS_CONFIRMED]


def test_automatic_resolution_and_explicit_override(runtime):
    coordinator, _, _, now = runtime
    coordinator.entry.options = {"selected_supplier": "powerpeers"}
    assert coordinator._effective_price_resolution("auto", now) == "quarter_hour"
    assert coordinator._effective_price_resolution("hourly", now) == "hourly"


@pytest.mark.parametrize("key", ["selected_supplier", "price_resolution", *POWERPEERS_NUMBERS, *POWERPEERS_FLAGS, CONF_POWERPEERS_CONFIRMED])
def test_changed_contract_reloads_cached_prices(key):
    assert _requires_reload({key: "before"}, {key: "after"})
