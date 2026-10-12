"""Offline registry validation, date selection and legacy calculation contracts."""

import copy
import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from custom_components.nl_day_ahead_prices import supplier_registry as registry
from custom_components.nl_day_ahead_prices.calculations import calculate_all_in_price, calculate_supplier_fee
from custom_components.nl_day_ahead_prices.supplier_profiles import load_supplier_profiles

TODAY = date(2026, 9, 23)


def payload():
    return {
        "registry_version": 1,
        "country": "NL",
        "currency": "EUR",
        "suppliers": {
            "test": {
                "name": "Test",
                "tariffs": [
                    {
                        "valid_from": "2026-01-01",
                        "valid_until": None,
                        "purchase_fee_import": 0.02,
                        "purchase_fee_export": 0.01,
                        "fixed_monthly_fee_electricity": 5,
                        "vat_included": True,
                        "settlement_resolution": "hourly",
                        "source_type": "official",
                        "source_url": "https://example.com/tariffs",
                        "last_verified": "2026-09-23",
                        "notes": None,
                    }
                ],
            }
        },
    }


def test_load_bundled_and_preserve_supplier_ids():
    bundled = registry.load_registry()
    assert bundled.version == 1
    assert set(bundled.suppliers) == set(load_supplier_profiles())
    assert set(registry.get_supplier_profiles(TODAY)) == set(load_supplier_profiles())
    assert registry.get_supplier_tariff("does_not_exist", TODAY) is None


def test_period_boundaries_future_expiry_and_overlaps(caplog):
    raw = payload()
    first = raw["suppliers"]["test"]["tariffs"][0]
    first["valid_until"] = "2026-08-31"
    second = {**first, "valid_from": "2026-09-01", "valid_until": None, "purchase_fee_import": 0.03}
    raw["suppliers"]["test"]["tariffs"].append(second)
    parsed = registry.parse_registry(raw)
    assert registry.select_tariff(parsed, "test", date(2025, 12, 31)) is None
    assert registry.select_tariff(parsed, "test", date(2026, 8, 31)).purchase_fee_import == 0.02
    assert registry.select_tariff(parsed, "test", date(2026, 9, 1)).purchase_fee_import == 0.03
    assert registry.select_tariff(parsed, "test", date(2030, 1, 1)).purchase_fee_import == 0.03
    assert registry.select_tariff(parsed, "unknown", TODAY) is None
    first["valid_until"] = None
    overlapping = registry.parse_registry(raw)
    assert registry.select_tariff(overlapping, "test", TODAY).purchase_fee_import == 0.03
    assert "Overlapping" in caplog.text


def test_expired_record_not_selected():
    raw = payload()
    raw["suppliers"]["test"]["tariffs"][0]["valid_until"] = "2026-08-31"
    assert registry.select_tariff(registry.parse_registry(raw), "test", TODAY) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("valid_until", "2025-12-31"),
        ("valid_from", "nonsense"),
        ("purchase_fee_import", float("nan")),
        ("purchase_fee_import", -1),
        ("purchase_fee_export", float("inf")),
        ("vat_included", "false"),
        ("fixed_monthly_fee_electricity", -1),
        ("source_type", "made_up"),
        ("settlement_resolution", "daily"),
        ("source_url", "file:///tmp/file"),
    ],
)
def test_invalid_records_rejected(field, value):
    raw = payload()
    raw["suppliers"]["test"]["tariffs"][0][field] = value
    with pytest.raises(ValueError):
        registry.parse_registry(raw)


@pytest.mark.parametrize("contents", ["not JSON", "[]", '{"registry_version":99}', "{}"])
def test_invalid_registry_falls_back_offline(tmp_path, monkeypatch, contents):
    path = tmp_path / "registry.json"
    path.write_text(contents)
    monkeypatch.setattr(registry, "REGISTRY_FILE", path)
    registry.load_registry.cache_clear()
    try:
        assert registry.load_registry().suppliers == {}
        assert registry.get_supplier_tariff("tibber", TODAY).purchase_fee_import == 0.0248
        assert registry.get_supplier_tariff("tibber", TODAY).registry_source == "legacy"
    finally:
        registry.load_registry.cache_clear()


@pytest.mark.parametrize(
    "days,expected",
    [
        (0, "current"),
        (60, "current"),
        (61, "verification_recommended"),
        (120, "verification_recommended"),
        (121, "stale"),
    ],
)
def test_freshness_boundaries(days, expected):
    assert registry.tariff_freshness((TODAY - timedelta(days=days)).isoformat(), TODAY) == (expected, days)


@pytest.mark.parametrize("verified", [None, "bad", "2026-02-30", "2027-01-01"])
def test_unknown_freshness(verified):
    assert registry.tariff_freshness(verified, TODAY) == ("unknown", None)


def test_date_selection_is_local_and_not_cached(monkeypatch):
    registry.load_registry()
    load_supplier_profiles()
    monkeypatch.setattr(registry.Path, "read_text", Mock(side_effect=AssertionError("No disk reads after startup")))
    before = registry.get_supplier_tariff("tibber", datetime(2026, 8, 31, 21, 59, tzinfo=timezone.utc))
    after = registry.get_supplier_tariff("tibber", datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc))
    assert before.purchase_fee_import == 0.0248
    assert after.purchase_fee_import == 0.018
    with pytest.raises(ValueError):
        registry.market_date(datetime(2026, 9, 1))


def test_calculations_current_historical_vat_zero_custom():
    historical = registry.get_supplier_tariff("tibber", date(2026, 8, 31))
    current = registry.get_supplier_tariff("tibber", TODAY)
    exclusive = registry.get_supplier_tariff("energyzero", TODAY)
    assert calculate_supplier_fee(historical, 0.21) == 0.0248
    assert calculate_supplier_fee(current, 0.21) == 0.018
    assert calculate_supplier_fee(exclusive, 0.21) == pytest.approx(0.03388)
    assert exclusive.sell_fee_includes_vat
    assert calculate_all_in_price(0.1, 0.1108, current, 0.21) == pytest.approx(0.2498)
    zero = replace(current, purchase_fee_electricity=0, purchase_fee_import=0)
    assert calculate_supplier_fee(zero, 0.21) == 0
    custom = registry.get_supplier_tariff(
        "tibber", TODAY, custom=replace(current, purchase_fee_electricity=0.05, purchase_fee_import=0.05)
    )
    assert calculate_supplier_fee(custom, 0.21) == 0.05
    assert registry.tariff_metadata(custom, TODAY)["supplier_tariff_status"] == "custom"


@pytest.mark.parametrize(
    "supplier,day,resolution",
    [
        ("zonneplan", "2026-07-31", "hourly"),
        ("zonneplan", "2026-08-01", "quarter_hour"),
        ("tibber", "2026-09-23", "quarter_hour"),
        ("anwb_energie", "2026-09-23", "hourly"),
        ("energyzero", "2026-01-01", "quarter_hour"),
        ("greenchoice", "2026-09-23", "quarter_hour"),
    ],
)
def test_supplier_resolution(supplier, day, resolution):
    on = date.fromisoformat(day)
    profile = registry.get_supplier_tariff(supplier, on)
    assert registry.tariff_metadata(profile, on)["supplier_settlement_resolution"] == resolution


def test_source_quality_and_verification_not_silently_refreshed():
    profiles = registry.get_supplier_profiles(TODAY)
    assert profiles["eneco"].source_type == "secondary"
    assert (
        registry.tariff_metadata(profiles["vandebron"], TODAY)["supplier_tariff_status"] == "verification_recommended"
    )
    assert registry.tariff_metadata(profiles["tibber"], TODAY)["supplier_tariff_status"] == "current"
    assert registry.tariff_metadata(profiles["custom"], TODAY)["supplier_tariff_status"] == "custom"


def test_required_fields_and_version():
    raw = payload()
    for field in list(raw["suppliers"]["test"]["tariffs"][0]):
        broken = copy.deepcopy(raw)
        del broken["suppliers"]["test"]["tariffs"][0][field]
        with pytest.raises(ValueError):
            registry.parse_registry(broken)
    raw["registry_version"] = 2
    with pytest.raises(ValueError):
        registry.parse_registry(raw)
    assert json.loads(registry.REGISTRY_FILE.read_text())["registry_version"] == 1


def test_interval_tariffs_use_local_date_and_per_call_cache(monkeypatch):
    from custom_components.nl_day_ahead_prices import calculations
    from custom_components.nl_day_ahead_prices.models import PriceEntry

    profile = registry.get_supplier_tariff("tibber", date(2026, 8, 31))
    lookup = Mock(wraps=registry.get_supplier_tariff)
    monkeypatch.setattr(calculations, "get_supplier_tariff", lookup)
    prices = [PriceEntry(datetime(2026, 8, 31, 21, 45, tzinfo=timezone.utc) + timedelta(minutes=i * 15), 0.1)
              for i in range(4)]
    attrs = calculations.build_all_in_price_attributes_for_supplier(prices, 0.1108, profile, 0.21)
    assert attrs[0]["price"] == pytest.approx(0.2566)
    assert [item["price"] for item in attrs[1:]] == pytest.approx([0.2498] * 3)
    assert lookup.call_count == 2
    assert [call.args[1] for call in lookup.call_args_list] == [date(2026, 8, 31), date(2026, 9, 1)]
    calculations.build_all_in_price_attributes_for_supplier(prices, 0.1108, profile, 0.21)
    assert lookup.call_count == 4
    custom = replace(profile, key="custom", purchase_fee_electricity=0.04)
    lookup.reset_mock()
    values = calculations.build_all_in_price_attributes_for_supplier(prices, 0.1108, custom, 0.21)
    lookup.assert_not_called()
    assert [item["price"] for item in values] == pytest.approx([0.2718] * 4)
