"""Pure cache validation helpers."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo


def get_cached_prices_for_date(cached: dict[str, Any] | None, target_date: date) -> dict[str, Any]:
    """Select valid intervals by Amsterdam date, independently of cache labels."""
    cached = cached or {}
    zone = ZoneInfo("Europe/Amsterdam")

    def select(keys):
        entries = {}
        for key in keys:
            items = cached.get(key, [])
            if not isinstance(items, list):
                continue
            for item in items:
                try:
                    timestamp = datetime.fromisoformat(item["time"])
                    price = float(item["price"])
                    if timestamp.tzinfo is None or not isfinite(price):
                        continue
                    if timestamp.astimezone(zone).date() == target_date:
                        entries[timestamp.astimezone(timezone.utc)] = {"time": timestamp.isoformat(), "price": price}
                except (ValueError, TypeError, KeyError):
                    continue
        return [entries[key] for key in sorted(entries)]

    prices = select(("prices_today", "prices_tomorrow"))
    raw = select(("raw_prices_today", "raw_prices_tomorrow"))
    prices = prices or raw
    promoted = bool(select(("prices_tomorrow", "raw_prices_tomorrow"))) and not bool(
        select(("prices_today", "raw_prices_today"))
    )
    start = datetime.combine(target_date, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
    minutes = (
        15
        if cached.get("effective_price_resolution") == "quarter_hour"
        or any(datetime.fromisoformat(item["time"]).minute % 60 for item in prices)
        else 60
    )
    expected = {
        start + timedelta(minutes=offset) for offset in range(0, int((end - start).total_seconds() / 60), minutes)
    }
    actual = {datetime.fromisoformat(item["time"]).astimezone(timezone.utc) for item in prices}
    status = (
        "cache_unavailable"
        if not prices
        else "cache_partial"
        if not expected.issubset(actual)
        else "cache_promoted_tomorrow"
        if promoted
        else "cache_exact_day"
    )
    return {"prices": prices, "raw_prices": raw or prices, "status": status, "rollover_used": promoted}


def cache_is_valid(cached: dict[str, Any] | None, now: datetime) -> bool:
    """Accept cached intervals belonging to the current market date."""
    return bool(get_cached_prices_for_date(cached, now.astimezone(ZoneInfo("Europe/Amsterdam")).date())["prices"])
