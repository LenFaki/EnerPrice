"""Data models and price helpers for EnerPrice."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class PriceEntry:
    """One market price entry in EUR/kWh."""

    time: datetime
    price: float

    def as_attribute(self) -> dict[str, Any]:
        """Return an ApexCharts-friendly attribute payload."""
        return {"time": self.time.isoformat(), "price": round(self.price, 6)}


@dataclass
class ProviderResult:
    """Normalized provider payload."""

    provider: str
    prices_today: list[PriceEntry]
    prices_tomorrow: list[PriceEntry]
    raw_today: Any = None
    raw_tomorrow: Any = None
    raw_prices_today: list[PriceEntry] | None = None
    raw_prices_tomorrow: list[PriceEntry] | None = None
    raw_price_resolution: str = "hourly"
    requested_price_resolution: str = "auto"
    effective_price_resolution: str = "hourly"
    resolution_converted: bool = False

    @property
    def prices(self) -> list[PriceEntry]:
        """Return today and tomorrow prices."""
        # Absolute time preserves interval order across the repeated DST hour.
        return sorted([*self.prices_today, *self.prices_tomorrow], key=lambda item: item.time.timestamp())

    @property
    def raw_prices(self) -> list[PriceEntry]:
        """Return source prices for today and tomorrow before conversion."""
        return sorted([*self.source_prices_today, *self.source_prices_tomorrow], key=lambda item: item.time)

    @property
    def source_prices_today(self) -> list[PriceEntry]:
        """Return source prices for today before conversion."""
        return self.raw_prices_today if self.raw_prices_today is not None else self.prices_today

    @property
    def source_prices_tomorrow(self) -> list[PriceEntry]:
        """Return source prices for tomorrow before conversion."""
        return self.raw_prices_tomorrow if self.raw_prices_tomorrow is not None else self.prices_tomorrow


@dataclass
class PriceData:
    """Coordinator data exposed to entities."""

    result: ProviderResult
    fallback_used: bool
    last_successful_update: datetime | None
    from_cache: bool = False
    errors: dict[str, str] = field(default_factory=dict)
    cache_age_minutes: float | None = None
    data_completeness: str = "unknown"
    cache_rollover_used: bool = False
    cache_source_date: str | None = None

    @property
    def api_data_available(self) -> bool:
        """Current-day availability does not depend on tomorrow publication."""
        return bool(self.result.prices_today)


def convert_to_eur_kwh(value: float, unit: str) -> float:
    """Convert an energy price to EUR/kWh."""
    normalized_unit = unit.lower().replace(" ", "")
    if normalized_unit in {"eur/kwh", "€/kwh"}:
        return float(value)
    if normalized_unit in {"eur/mwh", "€/mwh"}:
        return float(value) / 1000
    raise ValueError(f"Unsupported price unit: {unit}")


def calculate_all_in_price(
    market_price: float,
    energy_tax_incl_vat: float,
    supplier_markup_excl_vat: float,
    vat: float,
) -> float:
    """Calculate the all-in price in EUR/kWh."""
    return market_price * (1 + vat) + energy_tax_incl_vat + supplier_markup_excl_vat * (1 + vat)


def current_price(prices: list[PriceEntry], now: datetime) -> float | None:
    """Return the price for the interval containing now."""
    sorted_prices = sorted(prices, key=lambda item: item.time)
    default_interval = _default_interval(sorted_prices)
    for index, entry in enumerate(sorted_prices):
        start = _align_timezone(entry.time, now)
        end = start + _entry_duration(sorted_prices, index, default_interval)
        if start <= now < end:
            return entry.price
    return None


def next_hour_price(prices: list[PriceEntry], now: datetime) -> float | None:
    """Return the price for the next full interval."""
    sorted_prices = sorted(prices, key=lambda item: item.time)
    interval = _default_interval(sorted_prices)
    next_interval = _next_interval_start(now, interval)
    for entry in sorted_prices:
        if _align_timezone(entry.time, next_interval) == next_interval:
            return entry.price
    return None


def average_price(prices: list[PriceEntry]) -> float | None:
    """Return the average price."""
    if not prices:
        return None
    return sum(entry.price for entry in prices) / len(prices)


def lowest_price(prices: list[PriceEntry]) -> PriceEntry | None:
    """Return the lowest price entry."""
    return min(prices, key=lambda entry: entry.price, default=None)


def highest_price(prices: list[PriceEntry]) -> PriceEntry | None:
    """Return the highest price entry."""
    return max(prices, key=lambda entry: entry.price, default=None)


def _align_timezone(value: datetime, reference: datetime) -> datetime:
    """Attach the reference timezone when a provider returns a naive timestamp."""
    if value.tzinfo is None and reference.tzinfo is not None:
        return value.replace(tzinfo=reference.tzinfo)
    return value


def _default_interval(prices: list[PriceEntry]) -> timedelta:
    for earlier, later in zip(prices, prices[1:]):  # noqa: B905 - Python 3.9 compatibility.
        if later.time > earlier.time:
            delta = later.time - earlier.time
            return timedelta(minutes=15) if delta <= timedelta(minutes=15) else timedelta(hours=1)
    return timedelta(hours=1)


def _entry_duration(prices: list[PriceEntry], index: int, default_interval: timedelta) -> timedelta:
    if index + 1 < len(prices):
        duration = prices[index + 1].time - prices[index].time
        if duration > timedelta():
            return duration
    return default_interval


def _next_interval_start(now: datetime, interval: timedelta) -> datetime:
    minutes = int(interval.total_seconds() // 60)
    if minutes >= 60:
        return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    floored_minute = now.minute - (now.minute % minutes)
    return now.replace(minute=floored_minute, second=0, microsecond=0) + interval
