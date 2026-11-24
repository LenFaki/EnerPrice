"""Explicit market scope: NL contract pricing or Belgian wholesale-only data."""

from .const import CONF_COUNTRY, CONF_CURRENCY, DEFAULT_COUNTRY
from .supplier_profiles import SupplierProfile

COUNTRIES = {"NL": "Nederland", "BE": "België — alleen marktprijzen / wholesale only"}


def entry_options(entry):
    return {**entry.data, **entry.options}


def market_country(options):
    value = str(options.get(CONF_COUNTRY, DEFAULT_COUNTRY)).upper()
    if value not in COUNTRIES:
        raise ValueError("Supported electricity markets are NL and BE")
    return value


def wholesale_only(entry):
    return market_country(entry_options(entry)) == "BE"


def validate_market(options):
    country = market_country(options)
    if country == "BE" and options.get(CONF_CURRENCY, "EUR") != "EUR":
        raise ValueError("Belgian wholesale prices are available in EUR only")
    return country


def pricing_metadata(entry):
    metadata = {"market_country": market_country(entry_options(entry)),
                "pricing_mode": "wholesale_only" if wholesale_only(entry) else "contract"}
    if wholesale_only(entry):
        metadata.update(price_basis="wholesale_ex_vat", taxes_and_supplier_fees_included=False)
    return metadata


def wholesale_profile():
    """A zero-adjustment calculation basis, never a Belgian supplier contract."""
    return SupplierProfile(
        key="custom", name="BE wholesale market — excludes VAT, taxes and supplier fees",
        monthly_fee_electricity=0, purchase_fee_electricity=0, purchase_fee_unit="EUR_PER_KWH",
        purchase_fee_includes_vat=True, sell_fee_electricity=0, sell_fee_includes_vat=True,
        last_verified=None, source_url=None, price_resolution="quarter_hour",
        registry_source="custom", sell_market_price_includes_vat=False,
    )


# Only entities with market/relative-price semantics are exposed in Belgian mode.
# All-in, supplier, export-revenue and heat-source comparisons remain NL-only.
BE_PRICE_KEYS = frozenset({
    "current_market_price", "next_hour_market_price", "average_price_today", "average_price_tomorrow",
    "lowest_price_today", "lowest_energy_price", "highest_price_today", "highest_energy_price",
    "time_of_lowest_price_today", "time_of_highest_price_today", "effective_price_resolution",
    "current_provider", "last_successful_update", "current_price_trend", "next_trend_change_time", "price_trajectory",
    "price_rating", "price_level", "volatility_today", "volatility_tomorrow", "volatility_next_24h",
    "best_price_period_start", "best_price_period_end", "best_price_period_remaining_minutes",
    "best_price_period_progress_percent", "next_best_price_period_start", "peak_price_period_start",
    "peak_price_period_end", "peak_price_period_remaining_minutes", "peak_price_period_progress_percent",
    "next_peak_price_period_start", *[f"next_{hours}_hour_average_price" for hours in (1, 2, 3, 4, 6, 8, 12, 24)],
})
