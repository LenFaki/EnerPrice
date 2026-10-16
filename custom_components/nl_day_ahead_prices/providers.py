"""Async price providers for EnerPrice."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from aiohttp import ClientError, ClientSession

from .const import (
    DEFAULT_COUNTRY,
    DEFAULT_CURRENCY,
    PROVIDER_ENERGY_CHARTS,
    PROVIDER_ENTSOE,
    PROVIDER_NORD_POOL,
    REQUEST_RETRIES,
    REQUEST_TIMEOUT,
)
from .models import PriceEntry, ProviderResult, convert_to_eur_kwh
from .price_resolution import infer_price_resolution

_LOGGER = logging.getLogger(__name__)

ENTSOE_NL_DOMAIN = "10YNL----------L"


class ProviderError(Exception):
    """Raised when a provider cannot return usable prices."""

    def __init__(self, message: str, errors: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or {}


class BasePriceProvider(ABC):
    """Base class for asynchronous day-ahead price providers."""

    key: str

    def __init__(
        self,
        session: ClientSession,
        country: str = DEFAULT_COUNTRY,
        currency: str = DEFAULT_CURRENCY,
    ) -> None:
        self.session = session
        self.country = country
        self.currency = currency

    @abstractmethod
    async def async_fetch(self, today: date, tomorrow: date) -> ProviderResult:
        """Fetch normalized today and tomorrow prices."""

    async def _optional_day(self, day: date, parser):
        try:
            raw = await self._fetch_day(day)
            prices = parser(raw)
            if prices:
                return raw, prices
        except Exception as err:  # noqa: BLE001 - tomorrow must not invalidate today.
            status = getattr(err, "status", None)
            _LOGGER.debug("%s tomorrow fetch for %s: %s", self.key, day, f"HTTP {status}" if status else type(err).__name__)
        _LOGGER.debug("%s: Tomorrow prices are not available yet for %s; continuing with today prices", self.key, day)
        return None, []

    async def _request_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """Fetch JSON with timeout and retry."""
        last_error: Exception | None = None
        for attempt in range(REQUEST_RETRIES):
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    async with self.session.get(url, params=params) as response:
                        if response.status == 204:
                            return None
                        response.raise_for_status()
                        data = await response.json(content_type=None)
                        if data is None:
                            raise ValueError("empty JSON response")
                        return data
            except (TimeoutError, ClientError, ValueError) as err:
                last_error = err
                if attempt + 1 < REQUEST_RETRIES:
                    await asyncio.sleep(1)
        status = getattr(last_error, "status", None)
        raise ProviderError(f"HTTP {status}" if status else type(last_error).__name__) from last_error

    async def _request_text(self, url: str, params: dict[str, Any] | None = None) -> str:
        """Fetch text with timeout and retry."""
        last_error: Exception | None = None
        for attempt in range(REQUEST_RETRIES):
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    async with self.session.get(url, params=params) as response:
                        response.raise_for_status()
                        return await response.text()
            except (TimeoutError, ClientError) as err:
                last_error = err
                if attempt + 1 < REQUEST_RETRIES:
                    await asyncio.sleep(1)
        status = getattr(last_error, "status", None)
        raise ProviderError(f"HTTP {status}" if status else type(last_error).__name__) from last_error


class NordPoolProvider(BasePriceProvider):
    """Nord Pool day-ahead provider."""

    key = PROVIDER_NORD_POOL

    async def async_fetch(self, today: date, tomorrow: date) -> ProviderResult:
        today_raw = await self._fetch_day(today)
        prices_today = parse_nord_pool(today_raw, self.country)
        if not prices_today:
            raise ProviderError("today returned no prices")
        tomorrow_raw, prices_tomorrow = await self._optional_day(tomorrow, lambda raw: parse_nord_pool(raw, self.country))
        return ProviderResult(
            provider=self.key,
            prices_today=prices_today,
            prices_tomorrow=prices_tomorrow,
            raw_today=today_raw,
            raw_tomorrow=tomorrow_raw,
            raw_price_resolution=infer_price_resolution([*prices_today, *prices_tomorrow]),
        )

    async def _fetch_day(self, day: date) -> Any:
        return await self._request_json(
            "https://dataportal-api.nordpoolgroup.com/api/DayAheadPrices",
            {
                "date": day.isoformat(),
                "market": "DayAhead",
                "deliveryArea": self.country,
                "currency": self.currency,
            },
        )


class EnergyChartsProvider(BasePriceProvider):
    """Energy-Charts day-ahead provider."""

    key = PROVIDER_ENERGY_CHARTS

    async def async_fetch(self, today: date, tomorrow: date) -> ProviderResult:
        raw = await self._fetch_day(today)
        today_prices = parse_energy_charts(raw, today)
        if not today_prices:
            raise ProviderError("today returned no prices")
        tomorrow_raw, tomorrow_prices = await self._optional_day(tomorrow, lambda payload: parse_energy_charts(payload, tomorrow))
        return ProviderResult(
            provider=self.key,
            prices_today=today_prices,
            prices_tomorrow=tomorrow_prices,
            raw_today=raw,
            raw_tomorrow=tomorrow_raw,
            raw_price_resolution=infer_price_resolution([*today_prices, *tomorrow_prices]),
        )

    async def _fetch_day(self, day: date) -> Any:
        zone = ZoneInfo("Europe/Amsterdam")
        start = datetime.combine(day, time.min, tzinfo=zone)
        end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
        return await self._request_json("https://api.energy-charts.info/price", {
            "bzn": self.country, "start": start.isoformat(), "end": end.isoformat(),
        })


class EntsoeProvider(BasePriceProvider):
    """Optional ENTSO-E fallback provider."""

    key = PROVIDER_ENTSOE

    def __init__(
        self,
        session: ClientSession,
        api_token: str,
        country: str = DEFAULT_COUNTRY,
        currency: str = DEFAULT_CURRENCY,
    ) -> None:
        super().__init__(session, country, currency)
        self.api_token = api_token

    async def async_fetch(self, today: date, tomorrow: date) -> ProviderResult:
        today_raw = await self._fetch_day(today)
        prices_today = parse_entsoe_xml(today_raw)
        if not prices_today:
            raise ProviderError("today returned no prices")
        tomorrow_raw, prices_tomorrow = await self._optional_day(tomorrow, parse_entsoe_xml)
        return ProviderResult(
            provider=self.key,
            prices_today=prices_today,
            prices_tomorrow=prices_tomorrow,
            raw_today=today_raw,
            raw_tomorrow=tomorrow_raw,
            raw_price_resolution=infer_price_resolution([*prices_today, *prices_tomorrow]),
        )

    async def _fetch_day(self, day: date) -> str:
        zone = ZoneInfo("Europe/Amsterdam")
        start = datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc).strftime("%Y%m%d%H%M")
        end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc).strftime("%Y%m%d%H%M")
        return await self._request_text(
            "https://web-api.tp.entsoe.eu/api",
            {
                "securityToken": self.api_token,
                "documentType": "A44",
                "in_Domain": ENTSOE_NL_DOMAIN,
                "out_Domain": ENTSOE_NL_DOMAIN,
                "periodStart": start,
                "periodEnd": end,
            },
        )


async def async_fetch_with_fallback(
    providers: list[BasePriceProvider],
    today: date,
    tomorrow: date,
) -> tuple[ProviderResult, bool, dict[str, str]]:
    """Fetch prices from providers, falling back automatically."""
    errors: dict[str, str] = {}
    for index, provider in enumerate(providers):
        try:
            result = await provider.async_fetch(today, tomorrow)
            if not result.prices_today:
                raise ProviderError("provider returned no prices for today")
            return result, index > 0, errors
        except Exception as err:  # noqa: BLE001 - provider isolation is intentional.
            status = getattr(err, "status", None)
            detail = f"HTTP {status}" if status else str(err) if isinstance(err, ProviderError) else type(err).__name__
            following = providers[index + 1].key if index + 1 < len(providers) else "cache"
            _LOGGER.warning("Provider %s today fetch failed for %s: %s; trying %s", provider.key, today, detail, following)
            errors[provider.key] = detail
    raise ProviderError("all providers failed", errors)


def parse_nord_pool(payload: Any, area: str) -> list[PriceEntry]:
    """Parse Nord Pool's public day-ahead payload."""
    entries = payload.get("multiAreaEntries") or payload.get("MultiAreaEntries") or []
    prices: list[PriceEntry] = []
    for row in entries:
        price_value = row.get("entryPerArea", {}).get(area)
        if price_value is None:
            price_value = row.get("EntryPerArea", {}).get(area)
        if price_value is None:
            continue
        start = row.get("deliveryStart") or row.get("DeliveryStart") or row.get("time")
        if not start:
            continue
        start_dt = _parse_datetime(start)
        price = convert_to_eur_kwh(float(price_value), "EUR/MWh")
        prices.append(PriceEntry(start_dt, price))

    return sorted(prices, key=lambda entry: entry.time)


def parse_energy_charts(payload: Any, target_day: date) -> list[PriceEntry]:
    """Parse Energy-Charts price payload."""
    timestamps = payload.get("unix_seconds") or payload.get("time") or []
    values = payload.get("price") or payload.get("prices") or []
    prices: list[PriceEntry] = []
    for index, timestamp in enumerate(timestamps):
        if index >= len(values):
            break
        value = values[index]
        if value is None:
            continue
        dt = (
            datetime.fromtimestamp(timestamp, tz=timezone.utc)
            if isinstance(timestamp, (int, float))
            else datetime.fromisoformat(timestamp)
        )
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.astimezone(ZoneInfo("Europe/Amsterdam")).date() == target_day:
            prices.append(PriceEntry(dt, convert_to_eur_kwh(float(value), "EUR/MWh")))
    return sorted(prices, key=lambda entry: entry.time)


def _parse_datetime(value: str) -> datetime:
    """Parse provider datetimes across supported Python versions."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_entsoe_xml(payload: str) -> list[PriceEntry]:
    """Parse ENTSO-E XML into EUR/kWh prices."""
    root = ElementTree.fromstring(payload)
    namespace = ""
    if root.tag.startswith("{"):
        namespace = root.tag.split("}")[0] + "}"
    prices: list[PriceEntry] = []
    for period in root.iter(f"{namespace}Period"):
        start_node = period.find(f"{namespace}timeInterval/{namespace}start")
        if start_node is None or not start_node.text:
            continue
        start = datetime.fromisoformat(start_node.text.replace("Z", "+00:00"))
        resolution = period.findtext(f"{namespace}resolution", default="PT60M")
        interval = _parse_entsoe_resolution(resolution)
        for point in period.findall(f"{namespace}Point"):
            position = point.findtext(f"{namespace}position")
            price = point.findtext(f"{namespace}price.amount")
            if position is None or price is None:
                continue
            prices.append(
                PriceEntry(
                    start + interval * (int(position) - 1),
                    convert_to_eur_kwh(float(price), "EUR/MWh"),
                )
            )
    return sorted(prices, key=lambda entry: entry.time)


def _parse_entsoe_resolution(value: str) -> timedelta:
    if value == "PT15M":
        return timedelta(minutes=15)
    if value == "PT30M":
        return timedelta(minutes=30)
    if value == "PT60M":
        return timedelta(hours=1)
    _LOGGER.debug("Unsupported ENTSO-E resolution %s, assuming hourly positions", value)
    return timedelta(hours=1)
