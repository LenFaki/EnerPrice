from datetime import date, datetime

import pytest

from custom_components.nl_day_ahead_prices.const import REQUEST_RETRIES
from custom_components.nl_day_ahead_prices.models import PriceEntry, ProviderResult
from custom_components.nl_day_ahead_prices.providers import BasePriceProvider, ProviderError, async_fetch_with_fallback


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


class _JsonResponse:
    status = 200

    def __init__(self, body: object) -> None:
        self._body = body

    async def __aenter__(self) -> "_JsonResponse":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self, content_type: object = None) -> object:
        return self._body


class _JsonSession:
    def __init__(self, *bodies: object) -> None:
        self._bodies = list(bodies)
        self.calls = 0

    def get(self, url: str, params: object = None) -> _JsonResponse:
        self.calls += 1
        return _JsonResponse(self._bodies.pop(0))


async def _no_sleep(_: float) -> None:
    return None


@pytest.mark.asyncio
async def test_null_json_body_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("custom_components.nl_day_ahead_prices.providers.asyncio.sleep", _no_sleep)
    session = _JsonSession(None, {"ok": True})

    assert await WorkingProvider(session)._request_json("https://example.invalid") == {"ok": True}
    assert session.calls == 2


@pytest.mark.asyncio
async def test_null_json_body_is_reported_as_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("custom_components.nl_day_ahead_prices.providers.asyncio.sleep", _no_sleep)
    session = _JsonSession(*[None] * REQUEST_RETRIES)

    with pytest.raises(ProviderError, match="ValueError"):
        await WorkingProvider(session)._request_json("https://example.invalid")
    assert session.calls == REQUEST_RETRIES

