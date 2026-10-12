"""Exercise coordinator behavior with small Home Assistant runtime doubles."""

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest

from custom_components.nl_day_ahead_prices.providers import ProviderError


@pytest.fixture
def runtime(monkeypatch):
    zone = ZoneInfo("Europe/Amsterdam")
    now = datetime(2026, 9, 8, 0, 5, tzinfo=zone)

    class Coordinator:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, hass, **kwargs):
            self.hass = hass
            self.update_interval = kwargs["update_interval"]
            self.async_update_listeners = Mock()
            self.async_request_refresh = AsyncMock()

    class Store:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, *args):
            self.async_load = AsyncMock()
            self.async_save = AsyncMock()

    track = Mock(side_effect=lambda *args, **kwargs: Mock())
    dt = SimpleNamespace(now=lambda: now, as_local=lambda value: value.astimezone(zone))
    modules = {
        "homeassistant": {},
        "homeassistant.config_entries": {"ConfigEntry": object},
        "homeassistant.core": {"HomeAssistant": object},
        "homeassistant.helpers": {},
        "homeassistant.helpers.aiohttp_client": {"async_get_clientsession": Mock()},
        "homeassistant.helpers.event": {"async_track_utc_time_change": track},
        "homeassistant.helpers.storage": {"Store": Store},
        "homeassistant.helpers.update_coordinator": {
            "DataUpdateCoordinator": Coordinator,
            "UpdateFailed": RuntimeError,
        },
        "homeassistant.util": {"dt": dt},
    }
    for name, attributes in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    name = "custom_components.nl_day_ahead_prices._coordinator_test"
    path = Path(__file__).parents[1] / "custom_components/nl_day_ahead_prices/coordinator.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    entry = SimpleNamespace(data={}, options={"price_resolution": "hourly"}, entry_id="test")
    coordinator = module.NLDayAheadPricesCoordinator(object(), entry)
    return coordinator, module, track, now


@pytest.mark.asyncio
async def test_coordinator_promotes_yesterday_cache(runtime, monkeypatch):
    coordinator, module, _, now = runtime
    start = now.replace(hour=0, minute=0).astimezone(timezone.utc)
    prices = [{"time": (start + timedelta(hours=i)).isoformat(), "price": 0.1} for i in range(24)]
    coordinator.store.async_load.return_value = {
        "local_date": "2026-09-07",
        "prices_tomorrow": prices,
        "raw_prices_tomorrow": prices,
        "raw_price_resolution": "hourly",
        "last_successful_update": "2026-09-07T15:00:00+02:00",
    }
    monkeypatch.setattr(coordinator, "_providers", lambda: [])
    monkeypatch.setattr(
        module,
        "async_fetch_with_fallback",
        AsyncMock(side_effect=ProviderError("all failed", {"nord_pool": "HTTP 503"})),
    )
    result = await coordinator._async_update_data()
    assert len(result.result.prices_today) == 24
    assert result.result.prices_tomorrow == []
    assert result.cache_rollover_used
    assert result.api_data_available
    assert result.cache_source_date == "2026-09-07"
    assert result.errors["nord_pool"] == "HTTP 503"
    assert result.data_completeness == "cache_promoted_tomorrow"


@pytest.mark.asyncio
async def test_boundary_updates_do_not_fetch_and_hourly_schedule_is_stable(runtime):
    coordinator, _, track, now = runtime
    await coordinator.async_start()
    assert coordinator.update_interval is None
    assert track.call_args_list[0].kwargs == {"minute": 7, "second": 0}
    assert track.call_args_list[1].kwargs == {"minute": 0, "second": 0}
    await coordinator._async_interval_boundary(now)
    coordinator.async_update_listeners.assert_called_once()
    coordinator.async_request_refresh.assert_not_awaited()
    await coordinator._async_scheduled_refresh(now)
    coordinator.async_request_refresh.assert_awaited_once()
    await coordinator.async_stop()
    assert coordinator._remove_refresh_listener is None
    assert coordinator._remove_interval_listener is None


async def test_registry_activation_recalculates_raw_data_without_fetch(runtime):
    from custom_components.nl_day_ahead_prices.models import PriceData, PriceEntry, ProviderResult

    coordinator, _, track, now = runtime
    raw = [PriceEntry(now.replace(minute=0) + timedelta(minutes=15 * i), 0.1 + i * 0.01) for i in range(4)]
    coordinator.data = PriceData(
        ProviderResult("test", [PriceEntry(raw[0].time, 0.115)], [],
                       raw_prices_today=raw, raw_prices_tomorrow=[], raw_price_resolution="quarter_hour"),
        False, now,
    )
    await coordinator.async_start()
    coordinator.entry.options["price_resolution"] = "quarter_hour"
    coordinator.analysis_cache["old"] = object()
    coordinator.registry_updated()
    assert coordinator.analysis_cache == {}
    assert coordinator.data.result.prices_today == raw
    assert coordinator.data.result.effective_price_resolution == "quarter_hour"
    assert track.call_args.kwargs == {"minute": [0, 15, 30, 45], "second": 0}
    coordinator.async_update_listeners.assert_called_once()
    coordinator.async_request_refresh.assert_not_awaited()
    await coordinator.async_stop()
