from datetime import datetime, timezone

import pytest

from custom_components.nl_day_ahead_prices.calculations import (
    build_sell_price_attributes,
    calculate_sell_price,
)
from custom_components.nl_day_ahead_prices.models import (
    PriceEntry,
    calculate_all_in_price,
    convert_to_eur_kwh,
    next_hour_price,
)
from custom_components.nl_day_ahead_prices.supplier_profiles import SupplierProfile


def test_convert_eur_mwh_to_eur_kwh() -> None:
    assert convert_to_eur_kwh(123.45, "EUR/MWh") == 0.12345


def test_convert_eur_kwh_is_unchanged() -> None:
    assert convert_to_eur_kwh(0.234, "EUR/kWh") == 0.234


def test_all_in_formula() -> None:
    assert calculate_all_in_price(0.1, 0.1108, 0.01653, 0.21) == 0.2518013


def test_next_hour_all_in_formula_example() -> None:
    market_price = 0.001
    energy_tax_incl_vat = 0.1108
    supplier_markup_excl_vat = 0.0152
    vat = 0.21

    assert calculate_all_in_price(
        market_price,
        energy_tax_incl_vat,
        supplier_markup_excl_vat,
        vat,
    ) == 0.130402


def test_next_hour_price_uses_next_full_hour_entry() -> None:
    prices = [
        PriceEntry(datetime(2026, 7, 2, 10, tzinfo=timezone.utc), 0.10),
        PriceEntry(datetime(2026, 7, 2, 11, tzinfo=timezone.utc), 0.20),
    ]

    assert next_hour_price(prices, datetime(2026, 7, 2, 10, 12, tzinfo=timezone.utc)) == 0.20


def test_next_hour_price_uses_next_full_quarter_entry() -> None:
    prices = [
        PriceEntry(datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc), 0.10),
        PriceEntry(datetime(2026, 7, 2, 10, 15, tzinfo=timezone.utc), 0.20),
        PriceEntry(datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc), 0.30),
    ]

    assert next_hour_price(prices, datetime(2026, 7, 2, 10, 12, tzinfo=timezone.utc)) == 0.20


def _supplier_profile(**overrides: object) -> SupplierProfile:
    defaults: dict[str, object] = {
        "key": "test",
        "name": "Test",
        "monthly_fee_electricity": 0.0,
        "purchase_fee_electricity": 0.0,
        "purchase_fee_unit": "EUR_PER_KWH",
        "purchase_fee_includes_vat": True,
        "sell_fee_electricity": 0.0,
        "sell_fee_includes_vat": True,
        "last_verified": None,
        "source_url": None,
    }
    defaults.update(overrides)
    return SupplierProfile(**defaults)


def test_sell_price_subtracts_export_and_feed_in_fee() -> None:
    profile = _supplier_profile(purchase_fee_export=0.02, feed_in_fee=0.01, sell_fee_includes_vat=True)

    assert calculate_sell_price(0.1, profile, 0.21) == pytest.approx(0.1 * 1.21 - 0.03)


def test_sell_price_grosses_up_export_fee_when_it_excludes_vat() -> None:
    profile = _supplier_profile(purchase_fee_export=0.02, sell_fee_includes_vat=False)

    assert calculate_sell_price(0.1, profile, 0.21) == pytest.approx(0.1 * 1.21 - 0.02 * 1.21)


def test_sell_price_without_supplier_profile_is_market_price_including_vat() -> None:
    assert calculate_sell_price(0.1, None, 0.21) == pytest.approx(0.121)


def test_build_sell_price_attributes_formats_time_and_rounds_price() -> None:
    prices = [PriceEntry(datetime(2026, 7, 2, 10, tzinfo=timezone.utc), 0.1)]
    profile = _supplier_profile(purchase_fee_export=0.02)

    result = build_sell_price_attributes(prices, profile, 0.21)

    assert result == [{"time": "2026-07-02T10:00:00+00:00", "price": round(0.1 * 1.21 - 0.02, 6)}]
