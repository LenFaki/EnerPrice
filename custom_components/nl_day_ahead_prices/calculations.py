"""Price calculations for EnerPrice."""

from __future__ import annotations

from typing import Any

from .supplier_profiles import SupplierProfile
from .supplier_profiles import normalize_supplier_profile as _normalize_supplier_profile


def calculate_supplier_fee(profile: SupplierProfile | dict[str, Any] | None, vat: float) -> float:
    """Return the supplier purchase fee including VAT in EUR/kWh."""
    normalized = normalize_supplier_profile(profile)
    if normalized is None:
        return 0.0
    if normalized.purchase_fee_includes_vat:
        return normalized.purchase_fee_electricity
    return normalized.purchase_fee_electricity * (1 + vat)


def calculate_all_in_price(
    market_price: float,
    energy_tax: float,
    supplier_profile: SupplierProfile | dict[str, Any] | None,
    vat: float,
) -> float:
    """Calculate the all-in electricity price in EUR/kWh."""
    return market_price * (1 + vat) + energy_tax + calculate_supplier_fee(supplier_profile, vat)


def calculate_monthly_fee(profile: SupplierProfile | dict[str, Any] | None) -> float:
    """Return monthly electricity fee."""
    normalized = normalize_supplier_profile(profile)
    return normalized.monthly_fee_electricity if normalized is not None else 0.0


def calculate_supplier_export_fee(profile: SupplierProfile | dict[str, Any] | None, vat: float) -> float:
    """Return export and feed-in fees including VAT in EUR/kWh."""
    normalized = normalize_supplier_profile(profile)
    if normalized is None:
        return 0.0
    export_fee = normalized.purchase_fee_export
    if not normalized.sell_fee_includes_vat:
        export_fee *= 1 + vat
    return export_fee + normalized.feed_in_fee + (normalized.imbalance_fee or 0.0)


def build_all_in_price_attributes(
    prices: list[Any],
    energy_tax: float,
    supplier_profile: SupplierProfile | dict[str, Any] | None,
    vat: float,
) -> list[dict[str, Any]]:
    """Build ApexCharts-friendly all-in price attributes."""
    return [
        {
            "time": price.time.isoformat(),
            "price": round(calculate_all_in_price(price.price, energy_tax, supplier_profile, vat), 6),
        }
        for price in prices
    ]


def calculate_sell_price(
    market_price: float,
    supplier_profile: SupplierProfile | dict[str, Any] | None,
    vat: float,
) -> float:
    """Calculate the feed-in (export) price in EUR/kWh."""
    return market_price * (1 + vat) - calculate_supplier_export_fee(supplier_profile, vat)


def build_sell_price_attributes(
    prices: list[Any],
    supplier_profile: SupplierProfile | dict[str, Any] | None,
    vat: float,
) -> list[dict[str, Any]]:
    """Build ApexCharts-friendly sell (export) price attributes."""
    return [
        {
            "time": price.time.isoformat(),
            "price": round(calculate_sell_price(price.price, supplier_profile, vat), 6),
        }
        for price in prices
    ]


def normalize_supplier_profile(profile: SupplierProfile | dict[str, Any] | None) -> SupplierProfile | None:
    """Normalize supplier profiles passed to calculations."""
    if profile is None:
        return None
    if isinstance(profile, SupplierProfile):
        return profile
    return _normalize_supplier_profile(profile)
