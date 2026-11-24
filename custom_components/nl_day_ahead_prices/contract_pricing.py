"""Powerpeers contract inputs and documented interval-price rules.

No universal monthly fee is invented: setup requires the customer's contract
values and VAT treatment. This is an interval estimate, not a year/month bill.
"""

from dataclasses import replace
from datetime import date
from math import isfinite

from .const import (
    CONF_CUSTOM_MONTHLY_FEE_ELECTRICITY,
    CONF_CUSTOM_PURCHASE_FEE_ELECTRICITY,
    CONF_CUSTOM_PURCHASE_FEE_INCLUDES_VAT,
    CONF_CUSTOM_SELL_FEE_ELECTRICITY,
    CONF_CUSTOM_SELL_FEE_INCLUDES_VAT,
    CONF_CUSTOM_SELL_MARKET_PRICE_INCLUDES_VAT,
)
from .supplier_profiles import normalize_supplier_profile

POWERPEERS = "powerpeers"
CONF_POWERPEERS_CONFIRMED = "powerpeers_contract_confirmed"
POWERPEERS_NUMBERS = (
    CONF_CUSTOM_MONTHLY_FEE_ELECTRICITY, CONF_CUSTOM_PURCHASE_FEE_ELECTRICITY,
    CONF_CUSTOM_SELL_FEE_ELECTRICITY,
)
POWERPEERS_FLAGS = (
    CONF_CUSTOM_PURCHASE_FEE_INCLUDES_VAT, CONF_CUSTOM_SELL_FEE_INCLUDES_VAT,
    CONF_CUSTOM_SELL_MARKET_PRICE_INCLUDES_VAT,
)


def validate_powerpeers(options):
    if options.get(CONF_POWERPEERS_CONFIRMED) is not True:
        raise ValueError("Confirm your Powerpeers contract settings")
    for key in POWERPEERS_NUMBERS:
        value = options.get(key)
        if type(value) not in (int, float) or not isfinite(value) or value < 0:
            raise ValueError(f"Invalid Powerpeers contract setting: {key}")
    if any(type(options.get(key)) is not bool for key in POWERPEERS_FLAGS):
        raise ValueError("Confirm the Powerpeers contract VAT treatment")


def powerpeers_profile(options):
    validate_powerpeers(options)
    profile = normalize_supplier_profile({
        "name": "Powerpeers (contract)",
        "purchase_fee_import": options[CONF_CUSTOM_PURCHASE_FEE_ELECTRICITY],
        "purchase_fee_includes_vat": options[CONF_CUSTOM_PURCHASE_FEE_INCLUDES_VAT],
        "purchase_fee_export": options[CONF_CUSTOM_SELL_FEE_ELECTRICITY],
        "sell_fee_includes_vat": options[CONF_CUSTOM_SELL_FEE_INCLUDES_VAT],
        "sell_market_price_includes_vat": options[CONF_CUSTOM_SELL_MARKET_PRICE_INCLUDES_VAT],
        "fixed_monthly_fee_electricity": options[CONF_CUSTOM_MONTHLY_FEE_ELECTRICITY],
        "price_resolution": "quarter_hour",
        "notes": "Customer-confirmed contract. Interval estimate; annual saldering and monthly zero floor are not a bill. From 2027 export excludes VAT and uses the documented 2027–2029 minimum before the selling fee.",
    }, POWERPEERS)
    # Prevent bundled/remote defaults replacing explicitly confirmed terms.
    return replace(profile, registry_source="custom", source_type="manual")


def powerpeers_export_price(market_price, profile, vat, day, *, include_fee=True):
    """Apply the 2027–2029 per-interval minimum, then the selling fee.

    Contract section 10.3: max(EPEX, 50% * (EPEX + import fee)), excluding VAT.
    Monthly settlement floors and annual volume netting need consumption history
    and are deliberately not presented as realized interval revenue.
    """
    from .calculations import calculate_supplier_export_fee, calculate_supplier_fee

    if day < date(2027, 1, 1):
        price = market_price * (1 + vat) if profile.sell_market_price_includes_vat else market_price
    elif day < date(2030, 1, 1):
        import_fee_ex_vat = calculate_supplier_fee(profile, vat) / (1 + vat)
        price = max(market_price, .5 * (market_price + import_fee_ex_vat))
    else:
        price = market_price
    return price - (calculate_supplier_export_fee(profile, vat) if include_fee else 0)
