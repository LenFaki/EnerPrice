"""Sensors for EnerPrice."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .advisor import build_price_advice
from .analysis.forecast import average_next_period
from .analysis.periods import active_and_next, find_price_periods
from .analysis.rating import price_ratings
from .analysis.trend import trend_for_prices
from .analysis.volatility import volatility
from .calculations import (
    all_in_entries_for_supplier,
    calculate_monthly_fee,
    calculate_supplier_export_fee,
    calculate_supplier_fee,
)
from .const import (
    CONF_ADVICE_LANGUAGE,
    CONF_ALLOW_BEST_RELAXATION,
    CONF_ALLOW_PEAK_RELAXATION,
    CONF_BEST_PERIOD_DURATION,
    CONF_BEST_PERIOD_FLEX,
    CONF_CHART_HELPERS,
    CONF_CUSTOM_MONTHLY_FEE_ELECTRICITY,
    CONF_CUSTOM_PURCHASE_FEE_ELECTRICITY,
    CONF_CUSTOM_PURCHASE_FEE_INCLUDES_VAT,
    CONF_CUSTOM_SELL_FEE_ELECTRICITY,
    CONF_CUSTOM_SELL_FEE_INCLUDES_VAT,
    CONF_CUSTOM_SUPPLIER_NAME,
    CONF_ELECTRIC_EFFICIENCY,
    CONF_ENERGY_TAX,
    CONF_ENERGY_TAX_INCL_VAT,
    CONF_EXTENDED_ATTRIBUTES,
    CONF_FLEXIBLE_LOAD_POWER_W,
    CONF_GAS_EFFICIENCY,
    CONF_GAS_KWH_PER_M3,
    CONF_GAS_PRICE_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_MINIMUM_GAP,
    CONF_PEAK_PERIOD_DURATION,
    CONF_PEAK_PERIOD_FLEX,
    CONF_SELECTED_SUPPLIER,
    CONF_SMART_SETUP_ENABLED,
    CONF_SOLAR_POWER_ENTITY,
    CONF_SOLAR_SURPLUS_THRESHOLD_W,
    CONF_STABLE_TREND_THRESHOLD,
    CONF_STRONG_TREND_THRESHOLD,
    CONF_SUPPLIER_MARKUP_EXCL_VAT,
    CONF_VAT,
    DEFAULT_ADVICE_LANGUAGE,
    DEFAULT_CUSTOM_MONTHLY_FEE_ELECTRICITY,
    DEFAULT_CUSTOM_PURCHASE_FEE_INCLUDES_VAT,
    DEFAULT_CUSTOM_SELL_FEE_ELECTRICITY,
    DEFAULT_CUSTOM_SELL_FEE_INCLUDES_VAT,
    DEFAULT_CUSTOM_SUPPLIER_NAME,
    DEFAULT_ELECTRIC_EFFICIENCY,
    DEFAULT_ENERGY_TAX,
    DEFAULT_GAS_EFFICIENCY,
    DEFAULT_GAS_KWH_PER_M3,
    DEFAULT_SELECTED_SUPPLIER,
    DEFAULT_SOLAR_SURPLUS_THRESHOLD_W,
    DEFAULT_SUPPLIER_MARKUP_EXCL_VAT,
    DEFAULT_VAT,
    DOMAIN,
    PROVIDER_NAMES,
)
from .coordinator import NLDayAheadPricesCoordinator
from .models import (
    PriceData,
    average_price,
    current_price,
    highest_price,
    lowest_price,
    next_hour_price,
)
from .price_resolution import (
    PRICE_RESOLUTION_HOURLY,
    PRICE_RESOLUTION_QUARTER_HOUR,
    find_cheapest_consecutive_block,
)
from .registry_status import registry_status, tariff_status
from .scoring import calculate_day_score, calculate_opportunity, calculate_price_score
from .smart_energy import build_smart_energy_advice
from .supplier_profiles import SupplierProfile, supplier_profile_to_dict
from .supplier_registry import get_supplier_tariff, supplier_update_mode, tariff_metadata

EUR_PER_KWH = f"EUR/{UnitOfEnergy.KILO_WATT_HOUR}"
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class NLPriceSensorDescription(SensorEntityDescription):
    """Price sensor description."""

    value_fn: Callable[[PriceData, datetime, ConfigEntry], Any]
    analysis_key: str | None = None


def _current_market(data: PriceData, now: datetime, entry: ConfigEntry) -> float | None:
    return current_price(data.result.prices, now)


def _next_hour(data: PriceData, now: datetime, entry: ConfigEntry) -> float | None:
    return next_hour_price(data.result.prices, now)


def _average_today(data: PriceData, now: datetime, entry: ConfigEntry) -> float | None:
    return average_price(_all_in_entries(data, entry, data.result.prices_today))


def _average_tomorrow(data: PriceData, now: datetime, entry: ConfigEntry) -> float | None:
    return average_price(_all_in_entries(data, entry, data.result.prices_tomorrow))


def _lowest_today(data: PriceData, now: datetime, entry: ConfigEntry) -> float | None:
    entry_data = lowest_price(_all_in_entries(data, entry, data.result.prices_today))
    return entry_data.price if entry_data else None


def _highest_today(data: PriceData, now: datetime, entry: ConfigEntry) -> float | None:
    entry_data = highest_price(_all_in_entries(data, entry, data.result.prices_today))
    return entry_data.price if entry_data else None


def _lowest_time_today(data: PriceData, now: datetime, entry: ConfigEntry) -> datetime | None:
    entry_data = lowest_price(data.result.prices_today)
    return entry_data.time if entry_data else None


def _highest_time_today(data: PriceData, now: datetime, entry: ConfigEntry) -> datetime | None:
    entry_data = highest_price(data.result.prices_today)
    return entry_data.time if entry_data else None


def _current_all_in(data: PriceData, now: datetime, entry: ConfigEntry) -> float | None:
    return current_price(_all_in_entries(data, entry), now)


def _next_hour_all_in(data: PriceData, now: datetime, entry: ConfigEntry) -> float | None:
    return next_hour_price(_all_in_entries(data, entry), now)


def _average_all_in_today(data: PriceData, now: datetime, entry: ConfigEntry) -> float | None:
    return _average_today(data, now, entry)


def _lowest_all_in_today(data: PriceData, now: datetime, entry: ConfigEntry) -> float | None:
    return _lowest_today(data, now, entry)


def _highest_all_in_today(data: PriceData, now: datetime, entry: ConfigEntry) -> float | None:
    return _highest_today(data, now, entry)


def _supplier_purchase_fee(data: PriceData, now: datetime, entry: ConfigEntry) -> float:
    return calculate_supplier_fee(_selected_supplier_profile(entry), _vat(entry))


def _supplier_monthly_fee(data: PriceData, now: datetime, entry: ConfigEntry) -> float:
    return calculate_monthly_fee(_selected_supplier_profile(entry))


def _selected_supplier(data: PriceData, now: datetime, entry: ConfigEntry) -> str:
    return _selected_supplier_profile(entry).name


def _effective_price_resolution(data: PriceData, now: datetime, entry: ConfigEntry) -> str:
    return data.result.effective_price_resolution


def _entry_options(entry: ConfigEntry) -> dict[str, Any]:
    return {**entry.data, **entry.options}


def _energy_tax(entry: ConfigEntry) -> float:
    options = _entry_options(entry)
    return float(options.get(CONF_ENERGY_TAX, options.get(CONF_ENERGY_TAX_INCL_VAT, DEFAULT_ENERGY_TAX)))


def _vat(entry: ConfigEntry) -> float:
    return float(_entry_options(entry).get(CONF_VAT, DEFAULT_VAT))


def _selected_supplier_key(entry: ConfigEntry) -> str:
    return str(_entry_options(entry).get(CONF_SELECTED_SUPPLIER, DEFAULT_SELECTED_SUPPLIER))


def _selected_supplier_profile(entry: ConfigEntry) -> SupplierProfile:
    key = _selected_supplier_key(entry)
    if key == "custom":
        return _custom_supplier_profile(entry)

    profile = get_supplier_tariff(key, dt_util.now(), mode=supplier_update_mode(_entry_options(entry)))
    if profile is not None:
        return profile

    _LOGGER.warning("Configured supplier profile %s is unavailable; falling back to custom supplier", key)
    return _custom_supplier_profile(entry)


def _custom_supplier_profile(entry: ConfigEntry) -> SupplierProfile:
    options = _entry_options(entry)
    vat = _vat(entry)
    legacy_markup_incl_vat = float(
        options.get(CONF_SUPPLIER_MARKUP_EXCL_VAT, DEFAULT_SUPPLIER_MARKUP_EXCL_VAT)
    ) * (1 + vat)
    purchase_fee = float(options.get(CONF_CUSTOM_PURCHASE_FEE_ELECTRICITY, legacy_markup_incl_vat))
    return SupplierProfile(
        key="custom",
        name=str(options.get(CONF_CUSTOM_SUPPLIER_NAME, DEFAULT_CUSTOM_SUPPLIER_NAME)),
        monthly_fee_electricity=float(
            options.get(CONF_CUSTOM_MONTHLY_FEE_ELECTRICITY, DEFAULT_CUSTOM_MONTHLY_FEE_ELECTRICITY)
        ),
        purchase_fee_electricity=purchase_fee,
        purchase_fee_unit="EUR_PER_KWH",
        purchase_fee_includes_vat=bool(
            options.get(CONF_CUSTOM_PURCHASE_FEE_INCLUDES_VAT, DEFAULT_CUSTOM_PURCHASE_FEE_INCLUDES_VAT)
        ),
        sell_fee_electricity=float(
            options.get(CONF_CUSTOM_SELL_FEE_ELECTRICITY, DEFAULT_CUSTOM_SELL_FEE_ELECTRICITY)
        ),
        sell_fee_includes_vat=bool(options.get(CONF_CUSTOM_SELL_FEE_INCLUDES_VAT, DEFAULT_CUSTOM_SELL_FEE_INCLUDES_VAT)),
        last_verified=None,
        source_url=None,
        price_resolution=PRICE_RESOLUTION_HOURLY,
        price_resolution_changes=[],
        default_price_resolution_before_change=None,
    )


def _provider(data: PriceData, now: datetime, entry: ConfigEntry) -> str:
    return PROVIDER_NAMES.get(data.result.provider, data.result.provider)


def _last_successful(data: PriceData, now: datetime, entry: ConfigEntry) -> datetime | None:
    return data.last_successful_update


def _all_in_entries(data: PriceData, entry: ConfigEntry, prices: list | None = None) -> list:
    return all_in_entries_for_supplier(
        data.result.prices if prices is None else prices,
        _energy_tax(entry), _selected_supplier_profile(entry), _vat(entry),
    )


def _cached_all_in_entries(
    coordinator: NLDayAheadPricesCoordinator,
    data: PriceData,
    entry: ConfigEntry,
) -> list:
    """Return all-in prices cached for the current coordinator cycle."""
    cached_analysis = getattr(coordinator, "cached_analysis", None)
    if cached_analysis is None:
        return _all_in_entries(data, entry)
    return cached_analysis("all_in_entries", lambda: _all_in_entries(data, entry))


def _cached_value(coordinator: NLDayAheadPricesCoordinator | None, key: str, factory):
    """Use the coordinator cache when available, with a direct-call fallback."""
    cached_analysis = getattr(coordinator, "cached_analysis", None)
    return cached_analysis(key, factory) if cached_analysis is not None else factory()


def _cached_trend(
    coordinator: NLDayAheadPricesCoordinator | None,
    prices: list,
    now: datetime,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    """Return trend analysis shared by sensors in the current interval."""
    def factory():
        return trend_for_prices(
            prices,
            now,
            float(runtime[CONF_STABLE_TREND_THRESHOLD]),
            float(runtime[CONF_STRONG_TREND_THRESHOLD]),
        )
    return _cached_value(coordinator, "trend", factory)


def _cached_ratings(
    coordinator: NLDayAheadPricesCoordinator | None,
    prices: list,
    now: datetime,
) -> tuple[str, str]:
    """Return price ratings shared by sensors in the current interval."""
    def factory():
        return price_ratings(current_price(prices, now), prices)
    return _cached_value(coordinator, "ratings", factory)


def _analysis_value(
    key: str,
    data: PriceData,
    now: datetime,
    entry: ConfigEntry,
    runtime: dict[str, Any],
    coordinator: NLDayAheadPricesCoordinator | None = None,
) -> Any:
    prices = (
        _cached_all_in_entries(coordinator, data, entry)
        if coordinator is not None
        else _all_in_entries(data, entry)
    )
    if key.startswith("v2:"):
        return _v2_data(key.removeprefix("v2:"), data, now, entry)["state"]
    if key.startswith("forecast_"):
        return average_next_period(prices, now, int(key.removeprefix("forecast_")))
    if key in {"trend", "trend_change", "trajectory"}:
        trend = _cached_trend(coordinator, prices, now, runtime)
        if key == "trend":
            return trend["trend"]
        if key == "trend_change":
            return trend["next_change_time"]
        return trend["trajectory"]
    if key in {"rating_3", "rating_5"}:
        ratings = _cached_ratings(coordinator, prices, now)
        return ratings[0] if key == "rating_3" else ratings[1]
    if key.startswith("volatility_"):
        if key == "volatility_today":
            selected = [item for item in prices if item in prices[: len(data.result.prices_today)]]
        elif key == "volatility_tomorrow":
            selected = prices[len(data.result.prices_today) :]
        else:
            selected = [item for item in prices if now <= item.time < now + timedelta(hours=24)]
        return volatility(selected)["level"]
    period_type, field = key.split(":", 1)
    periods = _periods(data, entry, runtime, peak=period_type == "peak")
    active, upcoming = active_and_next(periods, now)
    period = upcoming if field == "next_start" else active
    if field == "start":
        return period.start if period else None
    if field == "end":
        return period.end if period else None
    if field == "next_start":
        return period.start if period else None
    if field == "remaining":
        return max(0, round((period.end - now).total_seconds() / 60)) if period else 0
    if field == "progress":
        if period is None:
            return 0
        return round((now - period.start).total_seconds() / (period.end - period.start).total_seconds() * 100, 1)
    return None


def _v2_data(
    key: str,
    data: PriceData,
    now: datetime,
    entry: ConfigEntry,
    language: str = "en",
    all_in: list | None = None,
) -> dict[str, Any]:
    """Return state and attributes for an EnerPrice v2 sensor."""
    all_in = _all_in_entries(data, entry) if all_in is None else all_in
    market_current = current_price(data.result.prices, now)
    all_in_current = current_price(all_in, now)
    score = calculate_price_score(all_in_current, all_in)
    _, rating = price_ratings(all_in_current, all_in)
    trend = trend_for_prices(all_in, now)
    stats = volatility(all_in)
    if key == "price_score":
        return {"state": score["score"], **score}
    if key == "price_advisor":
        advice = build_price_advice(
            current_price=market_current,
            all_in_price=all_in_current,
            score=score,
            rating=rating,
            trend=trend["trend"],
            volatility=stats["level"],
            language=language,
            prices=all_in,
            now=now,
        )
        return {"state": advice["state"], **advice}
    if key in {"today_score", "tomorrow_score"}:
        selected = (
            all_in[: len(data.result.prices_today)]
            if key == "today_score"
            else all_in[len(data.result.prices_today) :]
        )
        result = calculate_day_score(selected, all_in, language)
        return {"state": result["state"], **result}
    if key == "energy_opportunity":
        result = calculate_opportunity(all_in, language)
        return {"state": result["state"], **result}
    sell_prices = _sell_entries(data, entry)
    if not sell_prices:
        return {"state": None}
    if key == "best_export_period":
        best = max(sell_prices, key=lambda item: item.price)
        return {"state": best.time, "price": best.price}
    if key == "worst_export_period":
        worst = min(sell_prices, key=lambda item: item.price)
        return {"state": worst.time, "price": worst.price}
    sell_current = current_price(sell_prices, now)
    export_score = calculate_price_score(-sell_current if sell_current is not None else None, [
        type(item)(item.time, -item.price) for item in sell_prices
    ])
    label = export_score["score_label"] or "normal"
    recommendation = (
        (
            "Lever beschikbare zonne-energie nu terug."
            if label in {"excellent", "good"}
            else "Gebruik energie zelf of stel batterijteruglevering uit."
        )
        if language.startswith("nl")
        else (
            "Export available solar energy now."
            if label in {"excellent", "good"}
            else "Prefer self-consumption or delay battery export."
        )
    )
    return {
        "state": label,
        "current_sell_price": sell_current,
        "recommendation": recommendation,
        **export_score,
    }


def _sell_entries(data: PriceData, entry: ConfigEntry) -> list:
    profile = _selected_supplier_profile(entry)
    fee = calculate_supplier_export_fee(profile, _vat(entry))
    return [type(item)(item.time, item.price * (1 + _vat(entry)) - fee) for item in data.result.prices]


def _periods(data: PriceData, entry: ConfigEntry, runtime: dict[str, Any], *, peak: bool, prices: list | None = None):
    duration_key = CONF_PEAK_PERIOD_DURATION if peak else CONF_BEST_PERIOD_DURATION
    flex_key = CONF_PEAK_PERIOD_FLEX if peak else CONF_BEST_PERIOD_FLEX
    relaxation_key = CONF_ALLOW_PEAK_RELAXATION if peak else CONF_ALLOW_BEST_RELAXATION
    return find_price_periods(
        _all_in_entries(data, entry) if prices is None else prices,
        int(runtime[duration_key]),
        peak=peak,
        flex_percent=float(runtime[flex_key]),
        minimum_gap_minutes=int(runtime[CONF_MINIMUM_GAP]),
        allow_relaxation=bool(runtime[relaxation_key]),
    )


def _advanced_sensor(
    key: str,
    *,
    analysis_key: str | None = None,
    unit: str | None = None,
    timestamp: bool = False,
    options: list[str] | None = None,
) -> NLPriceSensorDescription:
    return NLPriceSensorDescription(
        key=key,
        translation_key=key,
        native_unit_of_measurement=unit,
        device_class=(
            SensorDeviceClass.ENUM
            if options
            else SensorDeviceClass.TIMESTAMP
            if timestamp
            else None
        ),
        options=options,
        state_class=SensorStateClass.MEASUREMENT if unit and unit != "%" and unit != "min" else None,
        suggested_display_precision=4 if unit == EUR_PER_KWH else None,
        entity_registry_enabled_default=False,
        value_fn=lambda data, now, entry: None,
        analysis_key=analysis_key or key,
    )


def _v2_sensor(
    key: str,
    *,
    enabled: bool = False,
    timestamp: bool = False,
    options: list[str] | None = None,
) -> NLPriceSensorDescription:
    return NLPriceSensorDescription(
        key=key,
        translation_key=key,
        device_class=(
            SensorDeviceClass.ENUM
            if options
            else SensorDeviceClass.TIMESTAMP
            if timestamp
            else None
        ),
        options=options,
        entity_registry_enabled_default=enabled,
        value_fn=lambda data, now, entry: None,
        analysis_key=f"v2:{key}",
    )


SENSORS: tuple[NLPriceSensorDescription, ...] = (
    NLPriceSensorDescription(
        key="current_market_price",
        translation_key="current_market_price",
        native_unit_of_measurement=EUR_PER_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=_current_market,
    ),
    NLPriceSensorDescription(
        key="next_hour_market_price",
        translation_key="next_hour_market_price",
        native_unit_of_measurement=EUR_PER_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=_next_hour,
    ),
    NLPriceSensorDescription(
        key="average_price_today",
        translation_key="average_price_today",
        native_unit_of_measurement=EUR_PER_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=_average_today,
    ),
    NLPriceSensorDescription(
        key="average_price_tomorrow",
        translation_key="average_price_tomorrow",
        native_unit_of_measurement=EUR_PER_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=_average_tomorrow,
    ),
    NLPriceSensorDescription(
        key="lowest_price_today",
        translation_key="lowest_price_today",
        native_unit_of_measurement=EUR_PER_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=_lowest_today,
    ),
    NLPriceSensorDescription(
        key="lowest_energy_price",
        translation_key="lowest_energy_price",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_lowest_time_today,
    ),
    NLPriceSensorDescription(
        key="highest_price_today",
        translation_key="highest_price_today",
        native_unit_of_measurement=EUR_PER_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=_highest_today,
    ),
    NLPriceSensorDescription(
        key="highest_energy_price",
        translation_key="highest_energy_price",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_highest_time_today,
    ),
    NLPriceSensorDescription(
        key="time_of_lowest_price_today",
        translation_key="time_of_lowest_price_today",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_lowest_time_today,
    ),
    NLPriceSensorDescription(
        key="time_of_highest_price_today",
        translation_key="time_of_highest_price_today",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_highest_time_today,
    ),
    NLPriceSensorDescription(
        key="current_all_in_price",
        translation_key="current_all_in_price",
        native_unit_of_measurement=EUR_PER_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=_current_all_in,
    ),
    NLPriceSensorDescription(
        key="next_hour_all_in_price",
        translation_key="next_hour_all_in_price",
        native_unit_of_measurement=EUR_PER_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=_next_hour_all_in,
    ),
    NLPriceSensorDescription(
        key="average_all_in_price_today",
        translation_key="average_all_in_price_today",
        native_unit_of_measurement=EUR_PER_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=_average_all_in_today,
    ),
    NLPriceSensorDescription(
        key="lowest_all_in_price_today",
        translation_key="lowest_all_in_price_today",
        native_unit_of_measurement=EUR_PER_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=_lowest_all_in_today,
    ),
    NLPriceSensorDescription(
        key="highest_all_in_price_today",
        translation_key="highest_all_in_price_today",
        native_unit_of_measurement=EUR_PER_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=_highest_all_in_today,
    ),
    NLPriceSensorDescription(
        key="supplier_purchase_fee",
        translation_key="supplier_purchase_fee",
        native_unit_of_measurement=EUR_PER_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=_supplier_purchase_fee,
    ),
    NLPriceSensorDescription(
        key="supplier_monthly_fee",
        translation_key="supplier_monthly_fee",
        native_unit_of_measurement="EUR",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_supplier_monthly_fee,
    ),
    NLPriceSensorDescription(
        key="selected_supplier",
        translation_key="selected_supplier",
        value_fn=_selected_supplier,
    ),
    NLPriceSensorDescription(
        key="effective_price_resolution",
        translation_key="effective_price_resolution",
        value_fn=_effective_price_resolution,
    ),
    NLPriceSensorDescription(key="current_provider", translation_key="current_provider", value_fn=_provider),
    NLPriceSensorDescription(
        key="last_successful_update",
        translation_key="last_successful_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_successful,
    ),
    *tuple(
        _advanced_sensor(
            f"next_{hours}_hour_average_price",
            analysis_key=f"forecast_{hours}",
            unit=EUR_PER_KWH,
        )
        for hours in (1, 2, 3, 4, 6, 8, 12, 24)
    ),
    _advanced_sensor(
        "current_price_trend",
        analysis_key="trend",
        options=["strongly_falling", "falling", "stable", "rising", "strongly_rising"],
    ),
    _advanced_sensor("next_trend_change_time", analysis_key="trend_change", timestamp=True),
    _advanced_sensor(
        "price_trajectory",
        analysis_key="trajectory",
        options=["strongly_falling", "falling", "stable", "rising", "strongly_rising"],
    ),
    _advanced_sensor("price_rating", analysis_key="rating_3", options=["low", "normal", "high"]),
    _advanced_sensor(
        "price_level",
        analysis_key="rating_5",
        options=["very_cheap", "cheap", "normal", "expensive", "very_expensive"],
    ),
    _advanced_sensor("volatility_today", options=["low", "moderate", "high", "very_high"]),
    _advanced_sensor("volatility_tomorrow", options=["low", "moderate", "high", "very_high"]),
    _advanced_sensor("volatility_next_24h", options=["low", "moderate", "high", "very_high"]),
    _advanced_sensor("best_price_period_start", analysis_key="best:start", timestamp=True),
    _advanced_sensor("best_price_period_end", analysis_key="best:end", timestamp=True),
    _advanced_sensor("best_price_period_remaining_minutes", analysis_key="best:remaining", unit="min"),
    _advanced_sensor("best_price_period_progress_percent", analysis_key="best:progress", unit="%"),
    _advanced_sensor("next_best_price_period_start", analysis_key="best:next_start", timestamp=True),
    _advanced_sensor("peak_price_period_start", analysis_key="peak:start", timestamp=True),
    _advanced_sensor("peak_price_period_end", analysis_key="peak:end", timestamp=True),
    _advanced_sensor("peak_price_period_remaining_minutes", analysis_key="peak:remaining", unit="min"),
    _advanced_sensor("peak_price_period_progress_percent", analysis_key="peak:progress", unit="%"),
    _advanced_sensor("next_peak_price_period_start", analysis_key="peak:next_start", timestamp=True),
    _v2_sensor("price_advisor", enabled=True, options=["excellent", "good", "neutral", "avoid", "critical"]),
    _v2_sensor("price_score", enabled=True),
    _v2_sensor("today_score", options=["excellent", "good", "normal", "expensive", "volatile"]),
    _v2_sensor("tomorrow_score", options=["excellent", "good", "normal", "expensive", "volatile"]),
    _v2_sensor(
        "export_advisor",
        options=["excellent", "good", "normal", "expensive", "very_expensive"],
    ),
    _v2_sensor("best_export_period", timestamp=True),
    _v2_sensor("worst_export_period", timestamp=True),
    _v2_sensor(
        "energy_opportunity",
        enabled=True,
        options=["none", "small", "medium", "high", "exceptional"],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator: NLDayAheadPricesCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [NLDayAheadPriceSensor(coordinator, entry, description) for description in SENSORS]
    entities.extend(NLRegistryDiagnosticSensor(coordinator, entry, description) for description in REGISTRY_SENSORS)
    if _entry_options(entry).get(CONF_SMART_SETUP_ENABLED, False):
        entities.append(NLSmartEnergyAdvisorSensor(coordinator, entry))
    daily = getattr(coordinator, "daily_forecast", None)
    if daily is not None:
        from .daily_sensor import daily_advice_entities, daily_forecast_entities

        entities.extend(daily_forecast_entities(daily, entry))
        entities.extend(daily_advice_entities(coordinator, entry))
    _LOGGER.info("Adding %s EnerPrice sensor entities", len(entities))
    async_add_entities(entities)


class NLSmartEnergyAdvisorSensor(CoordinatorEntity[NLDayAheadPricesCoordinator], SensorEntity):
    """Live Smart Energy Advisor using price and configured Home Assistant inputs."""

    _attr_has_entity_name = True
    _attr_name = "Smart Energy Advisor"
    _attr_icon = "mdi:home-lightning-bolt"
    _unrecorded_attributes = frozenset({"daily_advisor"})

    def __init__(self, coordinator: NLDayAheadPricesCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_smart_energy_advisor"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "EnerPrice",
            "manufacturer": "EnerPrice",
        }
        self._result: dict[str, Any] | None = None

    @property
    def suggested_object_id(self) -> str:
        return "nl_day_ahead_smart_energy_advisor"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        options = _entry_options(self.entry)
        watched = [
            options.get(CONF_GRID_POWER_ENTITY),
            options.get(CONF_SOLAR_POWER_ENTITY),
            options.get(CONF_GAS_PRICE_ENTITY),
        ]
        entity_ids = [entity_id for entity_id in watched if entity_id]
        if entity_ids:
            self.async_on_remove(async_track_state_change_event(self.hass, entity_ids, self._async_input_changed))
        daily = getattr(self.coordinator, "daily_forecast", None)
        if daily is not None:
            self.async_on_remove(daily.async_add_listener(self.async_write_ha_state))

    def _async_input_changed(self, event: Any) -> None:
        """Refresh advice on the Home Assistant event loop."""
        self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)

    @callback
    def async_write_ha_state(self) -> None:
        """Invalidate the snapshot once per write, not between state and attributes."""
        self._result = None
        super().async_write_ha_state()

    def _current_result(self) -> dict[str, Any]:
        """Share one calculation between native_value and extra_state_attributes."""
        if self._result is None:
            self._result = self._advice()
        return self._result

    def _advice(self) -> dict[str, Any]:
        data = self.coordinator.data
        if data is None:
            return {"state": "normal", "recommendation": "Price data is not available."}
        options = _entry_options(self.entry)
        all_in = _cached_all_in_entries(self.coordinator, data, self.entry)
        now = dt_util.now()
        language = str(options.get(CONF_ADVICE_LANGUAGE, DEFAULT_ADVICE_LANGUAGE))
        if language == "auto":
            language = self.hass.config.language
        context = None
        if getattr(self.coordinator, "daily_forecast", None) is not None:
            from .daily_advisor import advisor_context

            context = advisor_context(self.coordinator)
        return build_smart_energy_advice(
            electricity_price=current_price(all_in, now),
            future_prices=all_in,
            now=now,
            gas_price_per_m3=_state_float_sensor(self.hass, options.get(CONF_GAS_PRICE_ENTITY)),
            solar_power_w=_state_float_sensor(self.hass, options.get(CONF_SOLAR_POWER_ENTITY)),
            grid_power_w=_state_float_sensor(self.hass, options.get(CONF_GRID_POWER_ENTITY)),
            flexible_load_power_w=options.get(CONF_FLEXIBLE_LOAD_POWER_W),
            electric_efficiency=float(options.get(CONF_ELECTRIC_EFFICIENCY, DEFAULT_ELECTRIC_EFFICIENCY)),
            gas_efficiency=float(options.get(CONF_GAS_EFFICIENCY, DEFAULT_GAS_EFFICIENCY)),
            gas_kwh_per_m3=float(options.get(CONF_GAS_KWH_PER_M3, DEFAULT_GAS_KWH_PER_M3)),
            solar_surplus_threshold_w=float(options.get(CONF_SOLAR_SURPLUS_THRESHOLD_W, DEFAULT_SOLAR_SURPLUS_THRESHOLD_W)),
            language=language,
            daily_context=context,
        )

    @property
    def native_value(self) -> str:
        return str(self._current_result()["state"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            key: (value.isoformat() if isinstance(value, datetime) else value)
            for key, value in self._current_result().items()
            if key != "state"
        }


def _state_float_sensor(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in {"unknown", "unavailable", "none", ""}:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


class NLDayAheadPriceSensor(CoordinatorEntity[NLDayAheadPricesCoordinator], SensorEntity):
    """EnerPrice sensor."""

    entity_description: NLPriceSensorDescription
    _attr_has_entity_name = True
    # Keep chart arrays available to dashboards/ApexCharts while preventing the
    # recorder from storing the same large payload on every price sensor.
    _unrecorded_attributes = frozenset({
        "prices", "prices_today", "prices_tomorrow",
        "all_in_prices", "all_in_prices_today", "all_in_prices_tomorrow",
        "raw_prices", "raw_prices_today", "raw_prices_tomorrow",
        "raw_today", "raw_tomorrow", "supplier_profile",
        "best_periods", "peak_periods",
        "cheapest_15_minutes", "cheapest_30_minutes", "cheapest_45_minutes",
        "cheapest_1_hour", "cheapest_2_hours", "cheapest_3_hours", "cheapest_4_hours",
    })

    def __init__(
        self,
        coordinator: NLDayAheadPricesCoordinator,
        entry: ConfigEntry,
        description: NLPriceSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "EnerPrice",
            "manufacturer": "EnerPrice",
        }

    @property
    def suggested_object_id(self) -> str | None:
        """Return stable v2 object IDs while preserving all v1 naming."""
        if self.entity_description.analysis_key and self.entity_description.analysis_key.startswith("v2:"):
            return f"nl_day_ahead_{self.entity_description.key}"
        return f"nl_day_ahead_prices_{self.entity_description.key}"

    @property
    def native_value(self) -> Any:
        """Return sensor value."""
        if self.coordinator.data is None:
            return None
        if self.entity_description.analysis_key:
            if self.entity_description.analysis_key.startswith("v2:"):
                key = self.entity_description.analysis_key.removeprefix("v2:")
                language = self.coordinator.hass.config.language
                result = self.coordinator.cached_analysis(
                    f"v2:{language}:{key}",
                    lambda: _v2_data(
                        key,
                        self.coordinator.data,
                        dt_util.now(),
                        self.entry,
                        language,
                        _cached_all_in_entries(self.coordinator, self.coordinator.data, self.entry),
                    ),
                )
                value = result["state"]
            else:
                value = _analysis_value(
                    self.entity_description.analysis_key,
                    self.coordinator.data,
                    dt_util.now(),
                    self.entry,
                    self.coordinator.runtime_options,
                    self.coordinator,
                )
        else:
            value = self.entity_description.value_fn(self.coordinator.data, dt_util.now(), self.entry)
        return round(value, 6) if isinstance(value, float) else value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return ApexCharts-friendly price attributes."""
        data = self.coordinator.data
        if data is None:
            return {}
        supplier_profile = _selected_supplier_profile(self.entry)
        all_in_entries = _cached_all_in_entries(self.coordinator, data, self.entry)
        today_count = len(data.result.prices_today)
        shared_price_attributes = _cached_value(
            self.coordinator,
            "shared_price_attributes",
            lambda: {
                "prices": [entry.as_attribute() for entry in data.result.prices],
                "all_in_prices": [entry.as_attribute() for entry in all_in_entries],
                "prices_today": [entry.as_attribute() for entry in data.result.prices_today],
                "prices_tomorrow": [entry.as_attribute() for entry in data.result.prices_tomorrow],
                "raw_prices": [entry.as_attribute() for entry in data.result.raw_prices],
                "raw_prices_today": [entry.as_attribute() for entry in data.result.source_prices_today],
                "raw_prices_tomorrow": [entry.as_attribute() for entry in data.result.source_prices_tomorrow],
            },
        )
        all_in_attributes = shared_price_attributes["all_in_prices"]
        base = {
            **shared_price_attributes,
            "all_in_prices_today": all_in_attributes[:today_count],
            "all_in_prices_tomorrow": all_in_attributes[today_count:],
            "price_resolution": data.result.effective_price_resolution,
            "requested_price_resolution": data.result.requested_price_resolution,
            "effective_price_resolution": data.result.effective_price_resolution,
            "raw_price_resolution": data.result.raw_price_resolution,
            "resolution_converted": data.result.resolution_converted,
            "provider": data.result.provider,
            "provider_name": PROVIDER_NAMES.get(data.result.provider, data.result.provider),
            "fallback_used": data.fallback_used,
            "cache_used": data.from_cache,
            "cache_age_minutes": round(data.cache_age_minutes, 1) if data.cache_age_minutes is not None else None,
            "data_completeness": data.data_completeness,
            "last_successful_update": data.last_successful_update.isoformat()
            if data.last_successful_update
            else None,
            "selected_supplier": supplier_profile.key,
            "selected_supplier_name": supplier_profile.name,
            **tariff_metadata(supplier_profile, dt_util.now()),
            "supplier_purchase_fee": round(calculate_supplier_fee(supplier_profile, _vat(self.entry)), 6),
            "supplier_monthly_fee": round(calculate_monthly_fee(supplier_profile), 2),
            "energy_tax": _energy_tax(self.entry),
            "vat": _vat(self.entry),
        }
        if not self.coordinator.runtime_options[CONF_EXTENDED_ATTRIBUTES]:
            return base

        now = dt_util.now()
        prices = data.result.prices
        current_index = next(
            (index for index, item in enumerate(prices) if item.time <= now and (index + 1 == len(prices) or now < prices[index + 1].time)),
            None,
        )
        current_entry = prices[current_index] if current_index is not None else None
        next_entry = prices[current_index + 1] if current_index is not None and current_index + 1 < len(prices) else None
        current_all_in = current_price(all_in_entries, now)
        rating_3, rating_5 = _cached_ratings(self.coordinator, all_in_entries, now)
        trend = _cached_trend(self.coordinator, all_in_entries, now, self.coordinator.runtime_options)
        day_stats = _cached_value(
            self.coordinator, "volatility_today", lambda: volatility(all_in_entries[:today_count])
        )
        base.update({
            "raw_today": data.result.raw_today,
            "raw_tomorrow": data.result.raw_tomorrow,
            "supplier_profile_last_verified": supplier_profile.last_verified,
            "supplier_profile_source_url": supplier_profile.source_url,
            "supplier_profile": supplier_profile_to_dict(supplier_profile),
            **_cheapest_block_attributes(data),
            "current_interval_start": current_entry.time.isoformat() if current_entry else None,
            "current_interval_end": next_entry.time.isoformat() if next_entry else None,
            "next_interval_start": next_entry.time.isoformat() if next_entry else None,
            "source_provider": data.result.provider,
            "market_price": current_entry.price if current_entry else None,
            "all_in_price": current_all_in,
            "supplier_fee": round(calculate_supplier_fee(supplier_profile, _vat(self.entry)), 6),
            "rating_3_level": rating_3,
            "rating_5_level": rating_5,
            "trend": trend["trend"],
            "trend_value_percent": trend["trend_value_percent"],
            "volatility": day_stats.get("level"),
            "day_min": day_stats.get("min_price"),
            "day_max": day_stats.get("max_price"),
            "day_average": day_stats.get("average_price"),
            "day_median": day_stats.get("median_price"),
        })
        if self.coordinator.runtime_options[CONF_CHART_HELPERS]:
            best_periods = _cached_value(
                self.coordinator,
                "best_periods",
                lambda: _periods(data, self.entry, self.coordinator.runtime_options, peak=False, prices=all_in_entries),
            )
            peak_periods = _cached_value(
                self.coordinator,
                "peak_periods",
                lambda: _periods(data, self.entry, self.coordinator.runtime_options, peak=True, prices=all_in_entries),
            )
            base["best_periods"] = [period.as_dict() for period in best_periods]
            base["peak_periods"] = [period.as_dict() for period in peak_periods]
        if self.entity_description.analysis_key and self.entity_description.analysis_key.startswith("volatility_"):
            if self.entity_description.analysis_key == "volatility_today":
                base.update(volatility(all_in_entries[: len(data.result.prices_today)]))
            elif self.entity_description.analysis_key == "volatility_tomorrow":
                base.update(volatility(all_in_entries[len(data.result.prices_today) :]))
            else:
                base.update(volatility([item for item in all_in_entries if now <= item.time < now + timedelta(hours=24)]))
        if self.entity_description.analysis_key and self.entity_description.analysis_key.startswith("v2:"):
            key = self.entity_description.analysis_key.removeprefix("v2:")
            language = self.coordinator.hass.config.language
            v2_attributes = dict(
                self.coordinator.cached_analysis(
                    f"v2:{language}:{key}",
                    lambda: _v2_data(key, data, now, self.entry, language, all_in_entries),
                )
            )
            v2_attributes.pop("state", None)
            base.update(v2_attributes)
        return base


def _cheapest_block_attributes(data: PriceData) -> dict[str, Any]:
    prices = data.result.prices_today
    if data.result.effective_price_resolution == PRICE_RESOLUTION_QUARTER_HOUR:
        durations = {
            "cheapest_15_minutes": 15,
            "cheapest_30_minutes": 30,
            "cheapest_45_minutes": 45,
            "cheapest_1_hour": 60,
            "cheapest_2_hours": 120,
            "cheapest_3_hours": 180,
            "cheapest_4_hours": 240,
        }
    else:
        durations = {
            "cheapest_1_hour": 60,
            "cheapest_2_hours": 120,
            "cheapest_3_hours": 180,
            "cheapest_4_hours": 240,
        }
    return {key: find_cheapest_consecutive_block(prices, minutes) for key, minutes in durations.items()}

REGISTRY_SENSORS = (
    NLPriceSensorDescription(
        key="supplier_tariff_status", translation_key="supplier_tariff_status",
        device_class=SensorDeviceClass.ENUM, entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
        options=["current", "verification_recommended", "stale", "unknown", "custom"],
        value_fn=lambda data, now, entry: None,
    ),
    NLPriceSensorDescription(
        key="supplier_registry_status", translation_key="supplier_registry_status",
        device_class=SensorDeviceClass.ENUM, entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
        options=["remote", "cached_remote", "bundled"],
        value_fn=lambda data, now, entry: None,
    ),
)


class NLRegistryDiagnosticSensor(NLDayAheadPriceSensor):
    """Registry diagnostics stay available even when market APIs are unavailable."""

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        attrs = self.extra_state_attributes
        return attrs["freshness"] if self.entity_description.key == "supplier_tariff_status" else attrs["active_source"]

    @property
    def extra_state_attributes(self):
        manager = getattr(self.coordinator, "registry_manager", None)
        if self.entity_description.key == "supplier_tariff_status":
            return tariff_status(_selected_supplier_profile(self.entry), dt_util.now(), manager)
        return registry_status(manager, supplier_update_mode(_entry_options(self.entry)))
