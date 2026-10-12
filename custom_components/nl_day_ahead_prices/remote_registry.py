"""Validated public registry transport and last-known-good storage."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import aiohttp

# Re-export the PR1 API while sharing dependency-free validation with maintenance tools.
from .registry_validation import (
    MAX_KWH_FEE as MAX_KWH_FEE,
)
from .registry_validation import (
    MAX_MONTHLY_FEE as MAX_MONTHLY_FEE,
)
from .registry_validation import (
    NUMERIC_TARIFF_BOUNDS as NUMERIC_TARIFF_BOUNDS,
)
from .registry_validation import (
    REQUIRED_SUPPLIERS as REQUIRED_SUPPLIERS,
)
from .registry_validation import (
    TARIFF_FIELDS as TARIFF_FIELDS,
)
from .registry_validation import (
    decode_registry as decode_registry,
)
from .registry_validation import (
    parse_remote_registry as parse_remote_registry,
)
from .registry_validation import (
    utc_timestamp as utc_timestamp,
)
from .supplier_registry import SupplierRegistry, activate_remote_registry, load_registry

REMOTE_REGISTRY_URL = "https://raw.githubusercontent.com/LenFaki/home-assistant-nl-day-ahead-prices/main/registry/supplier_tariffs.json"
REMOTE_CHECK_INTERVAL = timedelta(hours=24)
REMOTE_TIMEOUT_SECONDS = 20
MAX_REGISTRY_BYTES = 1_048_576
STORAGE_KEY = "nl_day_ahead_prices_supplier_registry"
_LOGGER = logging.getLogger(__name__)


class RegistryHTTPError(ValueError):
    """A non-success HTTP status, without exposing its body."""


class RemoteRegistryManager:
    """Serialize checks, persist before publication, keep failures isolated."""

    def __init__(self, session, store, bundled: SupplierRegistry, *, enabled=True, now=None):
        self.session = session
        self.store = store
        self.active = bundled
        self.bundled = bundled
        self.cached_registry = None
        self.source = "bundled"
        self.cached_source = "cached_remote"
        self.last_check_result = "never_checked" if enabled else "disabled"
        self.enabled = enabled
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.last_check: datetime | None = None
        self.last_success: datetime | None = None
        self.last_update: datetime | None = None
        self.payload: dict | None = None
        self.listeners: set[Callable[[], None]] = set()
        self.subscriptions: dict[Callable[[], None], bool] = {}
        self._lock = asyncio.Lock()
        self._loaded = False
        self.task = None

    async def async_load(self):
        """Load only local storage; network is never awaited during setup."""
        async with self._lock:
            if self._loaded:
                return
            try:
                cached = await self.store.async_load()
                if cached is not None:
                    if not isinstance(cached, dict):
                        raise ValueError("Invalid cache envelope")
                    candidate = None
                    if cached.get("registry") is not None:
                        candidate = parse_remote_registry(cached["registry"])
                    times = {}
                    for field in ("last_check", "last_success", "last_update"):
                        value = cached.get(field)
                        times[field] = utc_timestamp(value) if value is not None else None
                        if times[field] is not None and times[field] > self.now():
                            raise ValueError("Future cache timestamp")
                    self.last_check = times["last_check"]
                    self.last_success = times["last_success"]
                    self.last_update = times["last_update"]
                    result = cached.get("last_check_result")
                    if isinstance(result, str):
                        self.last_check_result = result
                    elif self.last_success is not None:
                        # Backward compatibility with v2.2.0 cache envelopes.
                        self.last_check_result = "success" if self.last_update == self.last_success else "not_modified"
                    if candidate is not None and candidate.revision > self.active.revision:
                        self.payload = cached["registry"]
                        self.cached_registry = candidate
                        if self.enabled:
                            self.active = candidate
                            self.source = "cached_remote"
                            activate_remote_registry(candidate)
            except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
                _LOGGER.warning("Supplier registry cache invalid/unavailable; using bundled data")
            self._loaded = True

    def notify(self):
        """Publish bounded status changes as well as registry activations."""
        for listener in tuple(self.listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001 - isolate callbacks from shared transport
                _LOGGER.exception("Supplier registry listener failed")

    def set_enabled(self, enabled):
        """Switch shared transport policy; per-entry views stay independent."""
        if enabled == self.enabled:
            return
        self.enabled = enabled
        self.active = self.bundled
        self.source = "bundled"
        if enabled and self.cached_registry is not None and self.cached_registry.revision > self.bundled.revision:
            self.active = self.cached_registry
            self.source = self.cached_source
        activate_remote_registry(self.active if self.source != "bundled" else None)
        self.last_check_result = "never_checked" if enabled else "disabled"

    def _envelope(self, payload, success, updated):
        return {
            "registry": payload,
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "last_success": success.isoformat() if success else None,
            "last_update": updated.isoformat() if updated else None,
            "last_check_result": self.last_check_result,
        }

    async def _download(self):
        async with asyncio.timeout(REMOTE_TIMEOUT_SECONDS):
            async with self.session.get(REMOTE_REGISTRY_URL, allow_redirects=False) as response:
                if response.status != 200:
                    raise RegistryHTTPError()
                if response.content_length is not None and response.content_length > MAX_REGISTRY_BYTES:
                    raise ValueError("Registry response too large")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    raw.extend(chunk)
                    if len(raw) > MAX_REGISTRY_BYTES:
                        raise ValueError("Registry response too large")
                return decode_registry(bytes(raw))

    async def async_check(self):
        async with self._lock:
            now = self.now()
            if not self.enabled or (self.last_check and now - self.last_check < REMOTE_CHECK_INTERVAL):
                return False
            self.last_check = now
            stage = "storage_error"
            try:
                # Persist attempt time before HTTP, including failed checks across restarts.
                await self.store.async_save(self._envelope(self.payload, self.last_success, self.last_update))
                stage = "network_error"
                payload = await self._download()
                stage = "validation_error"
                candidate = parse_remote_registry(payload)
                newest_revision = max(self.active.revision, self.cached_registry.revision if self.cached_registry else 0)
                if candidate.revision <= newest_revision:
                    stage = "storage_error"
                    self.last_check_result = "not_modified" if self.enabled else "disabled"
                    await self.store.async_save(self._envelope(self.payload, now, self.last_update))
                    self.last_success = now
                    if (candidate.revision == self.active.revision and self.source == "cached_remote"
                            and payload == self.payload):
                        self.source = self.cached_source = "remote"
                    self.notify()
                    return False
                stage = "storage_error"
                self.last_check_result = "success" if self.enabled else "disabled"
                await self.store.async_save(self._envelope(payload, now, now))
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as err:
                # Do not log payloads or response bodies, including remotely supplied strings.
                _LOGGER.warning("Supplier registry check failed (%s); retaining local data", type(err).__name__)
                self.last_check_result = (
                    "http_error" if isinstance(err, RegistryHTTPError) else
                    "network_error" if isinstance(err, (aiohttp.ClientError, TimeoutError)) else
                    "validation_error" if stage != "storage_error" and isinstance(
                        err, (ValueError, TypeError, KeyError, OverflowError, RecursionError)
                    ) else stage
                )
                if not self.enabled:
                    self.last_check_result = "disabled"
                if stage != "storage_error":
                    try:
                        await self.store.async_save(self._envelope(self.payload, self.last_success, self.last_update))
                    except OSError:
                        pass
                self.notify()
                return False
            self.payload = payload
            self.last_success = self.last_update = now
            self.cached_registry = candidate
            self.cached_source = "remote"
            self.last_check_result = "success" if self.enabled else "disabled"
            if self.enabled:
                self.active = candidate
                self.source = "remote"
                activate_remote_registry(candidate)
            self.notify()
            return True

    async def async_run(self):
        """Independent daily loop; managed and cancelled by the HA adapter."""
        while True:
            await self.async_check()
            remaining = REMOTE_CHECK_INTERVAL
            if self.last_check:
                remaining -= self.now() - self.last_check
            await asyncio.sleep(max(1, remaining.total_seconds()))


class ConfirmedStore:
    """Read back HA Store writes because Store logs some write errors itself."""

    def __init__(self, store, storage_errors):
        self.store = store
        self.storage_errors = storage_errors

    async def async_load(self):
        try:
            return await self.store.async_load()
        except self.storage_errors as err:
            raise OSError("Registry storage unavailable") from err

    async def async_save(self, data):
        try:
            await self.store.async_save(data)
            if await self.store.async_load() != data:
                raise OSError("Registry storage write not confirmed")
        except self.storage_errors as err:
            raise OSError("Registry storage unavailable") from err


async def async_get_manager(hass):
    """Share a manager without adding non-coordinator items to DOMAIN data."""
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from homeassistant.helpers.storage import Store

    if STORAGE_KEY not in hass.data:
        hass.data[STORAGE_KEY] = RemoteRegistryManager(
            async_get_clientsession(hass),
            ConfirmedStore(Store(hass, 1, STORAGE_KEY, atomic_writes=True), (HomeAssistantError, NotImplementedError)),
            load_registry(), enabled=False,
        )
    manager = hass.data[STORAGE_KEY]
    await manager.async_load()
    return manager


def async_update_subscription(hass, manager, listener, enabled):
    """Any Automatic entry keeps the one shared updater alive."""
    manager.subscriptions[listener] = enabled
    _sync_subscriptions(hass, manager)
    manager.notify()


def _sync_subscriptions(hass, manager):
    enabled = any(manager.subscriptions.values())
    manager.set_enabled(enabled)
    if enabled and manager.task is None:
        manager.task = hass.async_create_background_task(manager.async_run(), "EnerPrice supplier registry")
    elif not enabled and manager.task is not None:
        manager.task.cancel()
        manager.task = None


def async_subscribe(hass, manager, listener, *, enabled=True):
    """Subscribe after setup; the background task does not gate HA startup."""
    manager.listeners.add(listener)
    async_update_subscription(hass, manager, listener, enabled)

    def unsubscribe():
        manager.listeners.discard(listener)
        manager.subscriptions.pop(listener, None)
        _sync_subscriptions(hass, manager)
        manager.notify()

    return unsubscribe
