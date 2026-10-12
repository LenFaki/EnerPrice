from datetime import date, datetime, timedelta, timezone
from json import JSONDecodeError
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from aiohttp import ClientResponseError

from custom_components.nl_day_ahead_prices.cache import cache_is_valid, get_cached_prices_for_date
from custom_components.nl_day_ahead_prices.models import PriceData
from custom_components.nl_day_ahead_prices.providers import (
    EnergyChartsProvider,
    EntsoeProvider,
    NordPoolProvider,
    async_fetch_with_fallback,
    parse_energy_charts,
)

TODAY = date(2026, 9, 8)
TOMORROW = date(2026, 9, 9)
ZONE = ZoneInfo("Europe/Amsterdam")


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [None, {}, "", JSONDecodeError("empty", "", 0)])
async def test_nord_pool_optional_tomorrow(missing):
    provider = NordPoolProvider(None)
    today = {"multiAreaEntries": [{"deliveryStart": "2026-09-07T22:00:00Z", "entryPerArea": {"NL": 100}}]}
    provider._fetch_day = AsyncMock(side_effect=[today, missing])
    result, fallback, errors = await async_fetch_with_fallback([provider], TODAY, TOMORROW)
    assert result.prices_today[0].price == 0.1
    assert result.prices_tomorrow == []
    assert not fallback
    assert errors == {}
    assert PriceData(result, fallback, None).api_data_available


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [204, 404, 503])
async def test_nord_pool_tomorrow_http_status(status, monkeypatch):
    from custom_components.nl_day_ahead_prices import providers

    # Python 3.9 developer environments lack asyncio.timeout; HTTP behavior is
    # tested with the same async context protocol without waiting for timeouts.
    timeout = MagicMock()
    timeout.return_value.__aenter__ = AsyncMock()
    timeout.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(providers.asyncio, "timeout", timeout, raising=False)
    monkeypatch.setattr(providers, "REQUEST_RETRIES", 1)
    today = MagicMock(status=200)
    today.json = AsyncMock(
        return_value={"multiAreaEntries": [{"deliveryStart": "2026-09-07T22:00Z", "entryPerArea": {"NL": 100}}]}
    )
    tomorrow = MagicMock(status=status)
    if status >= 400:
        tomorrow.raise_for_status.side_effect = ClientResponseError(MagicMock(), (), status=status)
    contexts = []
    for response in (today, tomorrow):
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        contexts.append(context)
    session = MagicMock()
    session.get.side_effect = contexts
    result = await NordPoolProvider(session).async_fetch(TODAY, TOMORROW)
    assert result.prices_today
    assert not result.prices_tomorrow
    assert PriceData(result, False, None).api_data_available


@pytest.mark.asyncio
async def test_energy_charts_optional_tomorrow():
    provider = EnergyChartsProvider(None)
    provider._fetch_day = AsyncMock(side_effect=[{"time": ["2026-09-07T22:00:00+00:00"], "price": [100]}, {}])
    result = await provider.async_fetch(TODAY, TOMORROW)
    assert len(result.prices_today) == 1
    assert result.prices_tomorrow == []


@pytest.mark.asyncio
async def test_entsoe_optional_tomorrow():
    provider = EntsoeProvider(None, "token")
    provider._fetch_day = AsyncMock(
        side_effect=[
            "<root><Period><timeInterval><start>2026-09-07T22:00Z</start></timeInterval><Point><position>1</position><price.amount>100</price.amount></Point></Period></root>",
            "",
        ]
    )
    result = await provider.async_fetch(TODAY, TOMORROW)
    assert len(result.prices_today) == 1
    assert result.prices_tomorrow == []


def test_cache_rollover_and_stale_rejection():
    start = datetime(2026, 9, 8, tzinfo=ZONE).astimezone(timezone.utc)
    prices = [{"time": (start + timedelta(hours=i)).isoformat(), "price": 0.1} for i in range(24)]
    cached = {"local_date": "2026-09-07", "prices_tomorrow": prices, "raw_prices_tomorrow": prices}
    restored = get_cached_prices_for_date(cached, TODAY)
    assert restored["prices"] == prices
    assert restored["raw_prices"] == prices
    assert restored["status"] == "cache_promoted_tomorrow"
    assert restored["rollover_used"]
    assert cache_is_valid(cached, start + timedelta(minutes=5))
    assert get_cached_prices_for_date(cached, TOMORROW)["status"] == "cache_unavailable"
    assert not cache_is_valid(cached, start + timedelta(days=1))
    assert get_cached_prices_for_date({"prices_today": prices}, TODAY)["status"] == "cache_exact_day"
    assert get_cached_prices_for_date({"raw_prices_today": prices[:1]}, TODAY)["status"] == "cache_partial"


def test_energy_charts_uses_local_midnight():
    payload = {
        "time": ["2026-09-07T21:00:00+00:00", "2026-09-07T22:00:00+00:00", "2026-09-08T22:00:00+00:00"],
        "price": [1, 2, 3],
    }
    result = parse_energy_charts(payload, TODAY)
    assert len(result) == 1
    assert result[0].price == 0.002
