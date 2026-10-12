from datetime import date, datetime

import pytest

from custom_components.nl_day_ahead_prices.models import PriceEntry, ProviderResult
from custom_components.nl_day_ahead_prices.providers import (
    BasePriceProvider,
    EntsoeProvider,
    NordPoolProvider,
    ProviderError,
    async_fetch_with_fallback,
)


class FailingProvider(BasePriceProvider):
    key = "failing"

    async def async_fetch(self, today: date, tomorrow: date) -> ProviderResult:
        raise ProviderError("boom")


class WorkingProvider(BasePriceProvider):
    key = "working"

    async def async_fetch(self, today: date, tomorrow: date) -> ProviderResult:
        return ProviderResult(
            provider=self.key,
            prices_today=[PriceEntry(datetime(2026, 7, 2), 0.1)],
            prices_tomorrow=[PriceEntry(datetime(2026, 7, 3), 0.2)],
        )


@pytest.mark.asyncio
async def test_provider_fallback_uses_second_provider() -> None:
    result, fallback_used, errors = await async_fetch_with_fallback(
        [FailingProvider(None), WorkingProvider(None)],
        date(2026, 7, 2),
        date(2026, 7, 3),
    )

    assert result.provider == "working"
    assert fallback_used is True
    assert errors == {"failing": "boom"}


@pytest.mark.asyncio
async def test_provider_fallback_raises_when_every_provider_fails() -> None:
    with pytest.raises(ProviderError):
        await async_fetch_with_fallback([FailingProvider(None)], date(2026, 7, 2), date(2026, 7, 3))


@pytest.mark.asyncio
async def test_nord_pool_tolerates_unpublished_tomorrow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Day-ahead providers only publish tomorrow's prices in the afternoon;
    that alone shouldn't fail the whole provider and trigger a fallback."""
    provider = NordPoolProvider(session=None, country="NL", currency="EUR")

    async def fake_fetch_day(day: date) -> dict:
        if day == date(2026, 7, 3):
            raise ProviderError("empty JSON response")
        return {
            "multiAreaEntries": [
                {"deliveryStart": "2026-07-02T00:00:00+02:00", "entryPerArea": {"NL": 100}}
            ]
        }

    monkeypatch.setattr(provider, "_fetch_day", fake_fetch_day)

    result = await provider.async_fetch(date(2026, 7, 2), date(2026, 7, 3))

    assert len(result.prices_today) == 1
    assert result.prices_tomorrow == []


@pytest.mark.asyncio
async def test_entsoe_tolerates_unpublished_tomorrow(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same "not yet published" tolerance applies to every provider that
    fetches today and tomorrow as separate requests, not just Nord Pool."""
    provider = EntsoeProvider(session=None, api_token="token", country="NL", currency="EUR")

    today_xml = """<Publication_MarketDocument>
        <TimeSeries>
            <Period>
                <timeInterval><start>2026-07-02T00:00Z</start></timeInterval>
                <resolution>PT60M</resolution>
                <Point><position>1</position><price.amount>100</price.amount></Point>
            </Period>
        </TimeSeries>
    </Publication_MarketDocument>"""

    async def fake_fetch_day(day: date) -> str:
        if day == date(2026, 7, 3):
            raise ProviderError("empty response")
        return today_xml

    monkeypatch.setattr(provider, "_fetch_day", fake_fetch_day)

    result = await provider.async_fetch(date(2026, 7, 2), date(2026, 7, 3))

    assert len(result.prices_today) == 1
    assert result.prices_tomorrow == []
    assert result.raw_tomorrow is None

