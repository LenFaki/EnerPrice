"""Bundled 2.1.1 tariff audit: amounts, history and conservative provenance."""

import json
import tomllib
from datetime import date, datetime, timezone
from itertools import combinations
from pathlib import Path

import pytest

from custom_components.nl_day_ahead_prices import supplier_registry as registry
from custom_components.nl_day_ahead_prices.calculations import (
    calculate_all_in_price,
    calculate_supplier_export_fee,
    calculate_supplier_fee,
)

AUDIT_DATE = date(2026, 9, 23)


@pytest.mark.parametrize(
    "supplier,fee,vat_included,monthly,resolution,inclusive_fee",
    [
        ("samsam", 0.021118, True, 7.99, "hourly", 0.021118),
        ("easy_energy", 0.02178, True, 7, "quarter_hour", 0.02178),
        ("anwb_energie", 0.018, True, 8.50, "hourly", 0.018),
        ("energyzero", 0.028, False, 7.50, "quarter_hour", 0.03388),
    ],
)
def test_confirmed_import_and_settlement(supplier, fee, vat_included, monthly, resolution, inclusive_fee):
    profile = registry.get_supplier_tariff(supplier, AUDIT_DATE)
    assert profile.purchase_fee_import == fee
    assert profile.purchase_fee_includes_vat is vat_included
    assert profile.fixed_monthly_fee_electricity == monthly
    assert profile.supports_hourly_prices
    assert profile.supports_quarter_hour_prices is (resolution == "quarter_hour")
    assert registry.tariff_metadata(profile, AUDIT_DATE)["supplier_settlement_resolution"] == resolution
    assert calculate_supplier_fee(profile, 0.21) == pytest.approx(inclusive_fee)
    assert calculate_all_in_price(0.1, 0.1108, profile, 0.21) == pytest.approx(
        0.121 + 0.1108 + inclusive_fee
    )


@pytest.mark.parametrize(
    "supplier,old_import,new_import,old_export,new_export,old_resolution,new_resolution",
    [
        ("samsam", 0.0339, 0.021118, 0.0339, 0.0339, "hourly", "hourly"),
        ("easy_energy", 0.0218, 0.02178, 0.0218, 0.0218, "hourly", "quarter_hour"),
    ],
)
def test_observation_boundaries(
    supplier, old_import, new_import, old_export, new_export, old_resolution, new_resolution
):
    before = registry.get_supplier_tariff(supplier, datetime(2026, 9, 22, 21, 59, tzinfo=timezone.utc))
    after = registry.get_supplier_tariff(supplier, datetime(2026, 9, 22, 22, 0, tzinfo=timezone.utc))
    assert before.valid_until == "2026-09-22"
    assert after.valid_from == "2026-09-23"
    assert before.purchase_fee_import == old_import
    assert after.purchase_fee_import == new_import
    assert before.purchase_fee_export == old_export
    assert after.purchase_fee_export == new_export
    assert calculate_supplier_export_fee(before, 0.21) == pytest.approx(old_export)
    assert calculate_supplier_export_fee(after, 0.21) == pytest.approx(new_export)
    assert registry.tariff_metadata(before, date(2026, 9, 22))["supplier_settlement_resolution"] == old_resolution
    assert registry.tariff_metadata(after, AUDIT_DATE)["supplier_settlement_resolution"] == new_resolution
    assert "observation date" in after.notes.lower()


def test_incomplete_records_are_not_promoted_to_current():
    for key, profile in registry.get_supplier_profiles(AUDIT_DATE).items():
        status = registry.tariff_metadata(profile, AUDIT_DATE)["supplier_tariff_status"]
        assert status == {
            "tibber": "current", "custom": "custom"
        }.get(key, "verification_recommended"), key
    for key in ("eneco", "vattenfall", "greenchoice"):
        assert registry.get_supplier_tariff(key, AUDIT_DATE).source_type == "secondary"
    for key in ("samsam", "easy_energy"):
        profile = registry.get_supplier_tariff(key, AUDIT_DATE)
        assert profile.source_type == "official"
        assert "unverified" in profile.notes
        assert profile.last_verified == "2026-07-02"


@pytest.mark.parametrize(
    "supplier,import_fee,export_fee,monthly",
    [
        ("zonneplan", 0.02, 0.02, 6.25),
        ("vandebron", 0.0257, 0.0257, 6.25),
        ("eneco", 0.0241, 0.0241, 7),
        ("vattenfall", 0.0255, 0.0255, 7.95),
        ("greenchoice", 0.0224, 0.0224, 7.5),
        ("pure_energie", 0.01699, -0.01299, 6.05),
        ("energyzero", 0.028, 0.0224, 7.5),
    ],
)
def test_unverified_amounts_are_preserved(supplier, import_fee, export_fee, monthly):
    profile = registry.get_supplier_tariff(supplier, AUDIT_DATE)
    assert profile.purchase_fee_import == import_fee
    assert profile.purchase_fee_export == export_fee
    assert profile.fixed_monthly_fee_electricity == monthly
    assert profile.sell_fee_includes_vat
    assert calculate_supplier_export_fee(profile, 0.21) == pytest.approx(export_fee)


def test_vattenfall_keeps_single_unverified_legacy_record():
    periods = registry.load_registry().suppliers["vattenfall"]
    assert len(periods) == 1
    profile = periods[0]
    assert profile.valid_from is None
    assert profile.valid_until is None
    assert profile.source_type == "secondary"
    assert profile.last_verified == "2026-07-02"
    assert registry.tariff_metadata(profile, AUDIT_DATE)["supplier_tariff_status"] == "verification_recommended"
    for day in (date(2026, 9, 22), AUDIT_DATE, date(2026, 9, 24)):
        assert registry.get_supplier_tariff("vattenfall", day) == profile
        assert profile.purchase_fee_import == 0.0255
        assert profile.purchase_fee_export == 0.0255
        assert profile.fixed_monthly_fee_electricity == 7.95


def test_bundled_json_validity_periods_and_versions(caplog):
    root = Path(__file__).resolve().parents[1]
    for path in (root / "custom_components").rglob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))
    parsed = registry.parse_registry(json.loads(registry.REGISTRY_FILE.read_text(encoding="utf-8")))
    assert len(parsed.suppliers) == 12
    for key, periods in parsed.suppliers.items():
        for first, second in combinations(periods, 2):
            start = max(first.valid_from or "0001-01-01", second.valid_from or "0001-01-01")
            end = min(first.valid_until or "9999-12-31", second.valid_until or "9999-12-31")
            assert start > end, f"Overlapping periods for {key}"
        for profile in periods:
            for boundary in (profile.valid_from, profile.valid_until):
                if boundary:
                    assert registry.select_tariff(parsed, key, date.fromisoformat(boundary)) == profile
        assert registry.select_tariff(parsed, key, AUDIT_DATE) is not None
    assert "Overlapping" not in caplog.text
    manifest = json.loads((registry.REGISTRY_FILE.parent / "manifest.json").read_text())
    project = tomllib.loads((root / "pyproject.toml").read_text())
    assert manifest["version"] == project["project"]["version"]
