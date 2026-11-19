"""Shared interval optimization primitives."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any

from .models import PriceEntry


def interval_minutes(prices: list[PriceEntry]) -> int:
    """Infer the shortest positive interval."""
    deltas = [
        int(_elapsed(later.time, earlier.time).total_seconds() / 60)
        for earlier, later in zip(prices, prices[1:])  # noqa: B905 - Python 3.9 compatibility.
        if later.time > earlier.time
    ]
    return min(deltas, default=60)


def available_between(
    prices: list[PriceEntry],
    earliest_start: datetime,
    deadline: datetime,
) -> list[PriceEntry]:
    """Return complete intervals inside an aware planning range."""
    _require_aware(earliest_start, "earliest_start")
    _require_aware(deadline, "deadline")
    if deadline <= earliest_start:
        raise ValueError("deadline must be after earliest_start")
    minutes = interval_minutes(prices)
    return sorted(
        [
            item
            for item in prices
            if _utc(item.time) >= _utc(earliest_start)
            and _utc(add_minutes(item.time, minutes)) <= _utc(deadline)
        ],
        key=lambda item: _utc(item.time),
    )


def optimize_intervals(
    prices: list[PriceEntry],
    duration_minutes: int,
    *,
    require_consecutive: bool = True,
    alternatives: int = 3,
    resolution_minutes: int | None = None,
    window_filter: Callable[[list[PriceEntry]], bool] | None = None,
) -> dict[str, Any] | None:
    """Select the cheapest intervals for a requested duration."""
    if duration_minutes <= 0 or not prices:
        return None
    minutes = resolution_minutes if resolution_minutes is not None else interval_minutes(prices)
    if minutes not in (15, 60) and resolution_minutes is not None:
        raise ValueError("Explicit resolution must be 15 or 60 minutes")
    count = ceil(duration_minutes / minutes)
    if len(prices) < count:
        return None
    if require_consecutive:
        candidates = _iter_consecutive_candidates(prices, count, minutes)
        if window_filter is not None:
            candidates = (candidate for candidate in candidates if window_filter(candidate))
        if alternatives == 0:
            # Daily planning only needs one window. Do not retain and sort every
            # candidate (up to 96 lists per day, repeated on live meter updates).
            selected = min(candidates, key=_average, default=None)
            if selected is None:
                return None
            other = []
        else:
            ranked = sorted(candidates, key=_average)
            if not ranked:
                return None
            selected = ranked[0]
            other = ranked[1 : alternatives + 1]
    else:
        if window_filter is not None:
            raise ValueError("Window filters require consecutive planning")
        selected = sorted(prices, key=lambda item: (item.price, item.time))[:count]
        selected.sort(key=lambda item: item.time)
        other = []
    return {
        **window_payload(selected, minutes),
        "alternatives": [window_payload(entries, minutes) for entries in other],
    }


def window_payload(entries: list[PriceEntry], minutes: int | None = None) -> dict[str, Any]:
    """Serialize selected intervals."""
    minutes = minutes or interval_minutes(entries)
    duration = len(entries) * minutes
    average = _average(entries)
    return {
        "recommended_start": entries[0].time,
        "recommended_end": add_minutes(entries[-1].time, minutes),
        "duration_minutes": duration,
        "average_price": round(average, 6),
        "selected_intervals": [item.as_attribute() for item in entries],
    }


def _consecutive_candidates(prices: list[PriceEntry], count: int, minutes: int) -> list[list[PriceEntry]]:
    return list(_iter_consecutive_candidates(prices, count, minutes))


def _iter_consecutive_candidates(prices: list[PriceEntry], count: int, minutes: int):
    """Stream windows, checking each adjacent boundary once in absolute time."""
    ordered = sorted(prices, key=lambda item: _utc(item.time))
    run = 0
    step = timedelta(minutes=minutes)
    for index, item in enumerate(ordered):
        run = run + 1 if index and _elapsed(item.time, ordered[index - 1].time) == step else 1
        if run >= count:
            yield ordered[index - count + 1:index + 1]


def _average(entries: list[PriceEntry]) -> float:
    return sum(item.price for item in entries) / len(entries)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def add_minutes(value: datetime, minutes: int) -> datetime:
    """Add physical elapsed minutes while preserving the displayed timezone."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value + timedelta(minutes=minutes)
    return (value.astimezone(timezone.utc) + timedelta(minutes=minutes)).astimezone(value.tzinfo)


def _elapsed(later: datetime, earlier: datetime) -> timedelta:
    return _utc(later) - _utc(earlier)


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo is not None else value
