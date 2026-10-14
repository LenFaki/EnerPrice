from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.nl_day_ahead_prices.analysis.chart_export import export_prices
from custom_components.nl_day_ahead_prices.analysis.forecast import average_next_period
from custom_components.nl_day_ahead_prices.analysis.periods import active_and_next, find_price_periods
from custom_components.nl_day_ahead_prices.analysis.rating import price_ratings
from custom_components.nl_day_ahead_prices.analysis.trend import classify_trend, trend_for_prices
from custom_components.nl_day_ahead_prices.analysis.volatility import volatility
from custom_components.nl_day_ahead_prices.cache import cache_is_valid
from custom_components.nl_day_ahead_prices.models import PriceEntry, current_price, next_hour_price


def _prices(count: int, minutes: int = 60, start: datetime | None = None) -> list[PriceEntry]:
    start = start or datetime(2026, 7, 9, tzinfo=timezone.utc)
    return [PriceEntry(start + timedelta(minutes=index * minutes), 0.10 + index * 0.01) for index in range(count)]


def test_forecast_average_hourly_and_quarter_hour() -> None:
    now = datetime(2026, 7, 9, tzinfo=timezone.utc)
    assert average_next_period(_prices(4), now, 2) == pytest.approx(0.105)
    assert average_next_period(_prices(8, 15), now, 2) == pytest.approx(0.135)


def test_trend_thresholds_and_change_time() -> None:
    now = datetime(2026, 7, 9, 0, 5, tzinfo=timezone.utc)
    prices = [
        PriceEntry(now.replace(minute=0), 0.10),
        PriceEntry(now.replace(minute=15), 0.12),
        PriceEntry(now.replace(minute=30), 0.121),
    ]
    result = trend_for_prices(prices, now, 2, 10)
    assert result["trend"] == "strongly_rising"
    assert result["next_change_time"] == prices[2].time
    assert classify_trend(-5, 2, 10) == "falling"


def test_price_ratings_cover_three_and_five_levels() -> None:
    prices = [PriceEntry(datetime(2026, 7, 9, index, tzinfo=timezone.utc), float(index)) for index in range(10)]
    assert price_ratings(0, prices) == ("low", "very_cheap")
    assert price_ratings(9, prices) == ("high", "very_expensive")


def test_volatility_statistics() -> None:
    result = volatility(_prices(4))
    assert result["min_price"] == 0.10
    assert result["max_price"] == 0.13
    assert result["median_price"] == 0.115
    assert result["level"] == "moderate"


def test_best_and_peak_period_detection_quarter_hour() -> None:
    start = datetime(2026, 7, 9, tzinfo=timezone.utc)
    values = [0.4, 0.1, 0.1, 0.4, 0.8, 0.9, 0.2, 0.2]
    prices = [PriceEntry(start + timedelta(minutes=15 * index), value) for index, value in enumerate(values)]
    best = find_price_periods(prices, 30)
    peak = find_price_periods(prices, 30, peak=True)
    assert best[0].start == start + timedelta(minutes=15)
    assert peak[0].start == start + timedelta(minutes=60)
    active, upcoming = active_and_next(best, start + timedelta(minutes=20))
    assert active == best[0]
    assert upcoming is None


def test_interval_boundary_selection_hourly_and_quarter_hour() -> None:
    hourly = _prices(3)
    assert current_price(hourly, hourly[0].time + timedelta(minutes=30)) == 0.10
    assert next_hour_price(hourly, hourly[0].time + timedelta(minutes=30)) == 0.11
    quarter = _prices(4, 15)
    assert current_price(quarter, quarter[1].time + timedelta(minutes=2)) == 0.11
    assert next_hour_price(quarter, quarter[1].time + timedelta(minutes=2)) == pytest.approx(0.12)


@pytest.mark.parametrize("day, expected", [(datetime(2026, 3, 29, tzinfo=timezone.utc), 23), (datetime(2026, 10, 25, tzinfo=timezone.utc), 25)])
def test_dst_day_counts_are_not_hardcoded(day: datetime, expected: int) -> None:
    amsterdam = ZoneInfo("Europe/Amsterdam")
    local_day = day.astimezone(amsterdam).date()
    entries = []
    cursor = datetime.combine(local_day, datetime.min.time(), tzinfo=amsterdam).astimezone(timezone.utc)
    while cursor.astimezone(amsterdam).date() == local_day:
        entries.append(PriceEntry(cursor.astimezone(amsterdam), 0.1))
        cursor += timedelta(hours=1)
    assert len(entries) == expected
    assert average_next_period(entries, entries[0].time, expected) == pytest.approx(0.1)


def test_cache_validation() -> None:
    now = datetime(2026, 7, 9, 12, tzinfo=timezone.utc)
    valid = {"local_date": "2026-07-09", "prices_today": [{"time": now.isoformat(), "price": 0.1}]}
    assert cache_is_valid(valid, now)
    assert cache_is_valid({**valid, "local_date": "2026-07-08"}, now)
    assert not cache_is_valid({**valid, "prices_today": []}, now)


def test_chart_export_conversion() -> None:
    exported = export_prices(_prices(1), "quarter_hour")
    assert len(exported) == 4
    assert set(exported[0]) == {"time", "price"}
