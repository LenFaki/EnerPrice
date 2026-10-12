"""Offline remote registry transport, storage and runtime regressions."""

import asyncio
import copy
import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import aiohttp
import pytest

from custom_components.nl_day_ahead_prices import registry_validation as validation
from custom_components.nl_day_ahead_prices import remote_registry as remote
from custom_components.nl_day_ahead_prices import supplier_registry as registry
from custom_components.nl_day_ahead_prices.calculations import build_all_in_price_attributes_for_supplier
from custom_components.nl_day_ahead_prices.models import PriceEntry, ProviderResult

NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def payload(revision=2):
    data = json.loads((ROOT / "registry/supplier_tariffs.json").read_text())
    data["revision"] = revision
    return data


@pytest.fixture(autouse=True)
def reset_active():
    registry.activate_remote_registry(None)
    yield
    registry.activate_remote_registry(None)


class Store:
    def __init__(self, data=None):
        self.data = copy.deepcopy(data)
        self.writes = []
        self.fail_revision = None

    async def async_load(self):
        return copy.deepcopy(self.data)

    async def async_save(self, data):
        candidate = data.get("registry")
        if candidate and candidate["revision"] == self.fail_revision:
            raise OSError("disk full")
        self.data = copy.deepcopy(data)
        self.writes.append(self.data)


class Response:
    def __init__(self, data, status=200, raw=None, length=None):
        self.raw = raw if raw is not None else json.dumps(data).encode()
        self.status = status
        self.content_length = length
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def iter_chunked(self, size):
        for offset in range(0, len(self.raw), size):
            yield self.raw[offset:offset + size]


def setup(cached=None, revision=2, **response_args):
    store = Store({"registry": payload(cached)} if cached else None)
    session = Mock()
    session.get.return_value = Response(payload(revision), **response_args)
    manager = remote.RemoteRegistryManager(session, store, registry.load_registry(), now=lambda: NOW)
    return manager, session, store


@pytest.mark.parametrize("cached,new", [(None, 2), (4, 5)])
async def test_valid_update_persists_before_activation(cached, new):
    manager, session, store = setup(cached, new)
    await manager.async_load()
    assert manager.active.revision == (cached or 1)
    session.get.assert_not_called()
    listener = Mock(side_effect=lambda: (
        store.data["registry"]["revision"] == manager.active.revision
    ) or pytest.fail("Published before persistence"))
    manager.listeners.add(listener)
    assert await manager.async_check()
    assert manager.active.revision == new
    assert store.data["registry"]["revision"] == new
    assert store.data["last_update"] == NOW.isoformat()
    listener.assert_called_once()
    session.get.assert_called_once_with(remote.REMOTE_REGISTRY_URL, allow_redirects=False)
    assert registry.get_supplier_tariff("tibber", NOW).registry_source == "remote"


async def test_restart_uses_cache_without_network_and_throttles():
    manager, session, store = setup(4, 5)
    store.data["last_check"] = NOW.isoformat()
    await manager.async_load()
    assert manager.active.revision == 4
    assert not await manager.async_check()
    session.get.assert_not_called()
    manager.now = lambda: NOW + timedelta(hours=24)
    assert await manager.async_check()
    assert manager.active.revision == 5


@pytest.mark.parametrize("revision", [1, 3, 4])
async def test_same_or_older_revision_preserves_snapshot(revision):
    manager, _, store = setup(4, revision)
    await manager.async_load()
    original = manager.active
    assert not await manager.async_check()
    assert manager.active is original
    assert store.data["registry"] == payload(4)
    assert store.data["last_update"] is None


@pytest.mark.parametrize("cached", [None, 4])
@pytest.mark.parametrize("failure", [TimeoutError(), aiohttp.ClientConnectionError(), 404, 500, 503, 302, b"{bad", b"[]"])
async def test_download_failure_preserves_local_data(cached, failure):
    manager, session, store = setup(cached)
    if isinstance(failure, Exception):
        session.get.side_effect = failure
    elif isinstance(failure, int):
        session.get.return_value.status = failure
    else:
        session.get.return_value.raw = failure
    await manager.async_load()
    original = manager.active
    assert not await manager.async_check()
    assert manager.active is original
    assert store.data["registry"] == (payload(4) if cached else None)
    # A restart after failure must not hammer the endpoint.
    restarted = remote.RemoteRegistryManager(session, store, registry.load_registry(), now=lambda: NOW)
    await restarted.async_load()
    assert not await restarted.async_check()
    assert session.get.call_count == 1


@pytest.mark.parametrize("field,value", [
    ("schema_version", 2), ("schema_version", True), ("revision", 0), ("revision", True),
    ("revision", 1.5), ("country", "DE"), ("currency", "USD"),
    ("published_at", "2026-09-23"), ("published_at", "2026-09-23T12:00:00+02:00"),
    ("suppliers", {}), ("suppliers", []),
])
async def test_invalid_envelope_preserves_good_cache(field, value):
    manager, session, store = setup(4, 5)
    candidate = payload(5)
    candidate[field] = value
    session.get.return_value = Response(candidate)
    await manager.async_load()
    assert not await manager.async_check()
    assert manager.active.revision == 4
    assert store.data["registry"] == payload(4)


@pytest.mark.parametrize("field,value", [
    ("valid_from", "2026-02-30"), ("valid_until", "2026-99-01"),
    ("last_verified", "yesterday"), ("last_verified", "20260923"),
    ("vat_included", 1), ("export_vat_included", "true"),
    ("supports_hourly_prices", None), ("supports_quarter_hour_prices", 0),
    ("purchase_fee_import", float("nan")), ("purchase_fee_import", float("inf")),
    ("purchase_fee_import", "0.02"), ("purchase_fee_import", True),
    ("purchase_fee_import", -0.01), ("purchase_fee_import", 25),
    ("purchase_fee_export", -50), ("fixed_monthly_fee_electricity", 7500),
    ("fixed_monthly_fee_electricity", -1), ("imbalance_fee", float("inf")),
    ("source_type", "verified"), ("source_url", "file:///tmp/x"),
    ("source_url", None), ("source_url", 123), ("source_url", "https://user:pass@example.com"),
    ("settlement_resolution", "daily"), ("profile_version", True),
])
def test_invalid_tariff(field, value):
    candidate = payload()
    candidate["suppliers"]["anwb_energie"]["tariffs"][0][field] = value
    with pytest.raises((ValueError, TypeError)):
        remote.parse_remote_registry(candidate)


def test_required_coverage_periods_and_fields():
    for key in remote.REQUIRED_SUPPLIERS:
        candidate = payload()
        del candidate["suppliers"][key]
        with pytest.raises(ValueError):
            remote.parse_remote_registry(candidate)
    for field in remote.TARIFF_FIELDS:
        candidate = payload()
        del candidate["suppliers"]["anwb_energie"]["tariffs"][0][field]
        with pytest.raises(ValueError):
            remote.parse_remote_registry(candidate)
    candidate = payload()
    records = candidate["suppliers"]["tibber"]["tariffs"]
    records[0]["valid_until"] = records[1]["valid_from"]
    with pytest.raises(ValueError, match="Overlapping"):
        remote.parse_remote_registry(candidate)
    records[0]["valid_from"] = "2026-10-01"
    with pytest.raises(ValueError):
        remote.parse_remote_registry(candidate)


@pytest.mark.parametrize("cached", [{"registry": {}}, [], {"registry": payload(4), "last_check": "bad"},
                                     {"registry": payload(4), "last_check": "2099-01-01T00:00:00Z"}])
async def test_corrupt_cache_ignored(cached):
    manager, session, store = setup()
    store.data = cached
    await manager.async_load()
    assert manager.active.revision == 1
    assert manager.payload is None
    session.get.assert_not_called()


async def test_storage_failure_does_not_activate_new_data():
    manager, _, store = setup(4, 5)
    await manager.async_load()
    store.fail_revision = 5
    assert not await manager.async_check()
    assert manager.active.revision == 4
    assert store.data["registry"] == payload(4)


@pytest.mark.parametrize("header", [None, remote.MAX_REGISTRY_BYTES + 1])
async def test_oversized_response(header):
    manager, _, _ = setup(4, raw=b" " * (remote.MAX_REGISTRY_BYTES + 1), length=header)
    await manager.async_load()
    assert not await manager.async_check()
    assert manager.active.revision == 4


async def test_concurrency_and_daily_interval():
    manager, session, _ = setup()
    await manager.async_load()
    results = await asyncio.gather(*(manager.async_check() for _ in range(10)))
    assert results.count(True) == 1
    assert session.get.call_count == 1
    manager.now = lambda: NOW + timedelta(hours=23, minutes=59)
    assert not await manager.async_check()
    manager.now = lambda: NOW + timedelta(hours=24)
    assert not await manager.async_check()  # same revision, but next request is due
    assert session.get.call_count == 2


def test_initial_remote_data_exactly_matches_bundle():
    candidate = payload(1)
    bundled = json.loads(registry.REGISTRY_FILE.read_text())
    assert candidate["suppliers"] == bundled["suppliers"]
    assert remote.parse_remote_registry(candidate).revision == 1
    assert remote.parse_remote_registry(candidate).suppliers["pure_energie"][0].purchase_fee_export == -0.01299


def test_custom_and_date_fallback():
    original = registry.get_supplier_tariff("custom", NOW)
    candidate = payload()
    candidate["suppliers"]["custom"]["tariffs"][0]["purchase_fee_import"] = 1
    records = candidate["suppliers"]["anwb_energie"]["tariffs"]
    records[0]["valid_from"] = "2026-10-01"
    records[0]["purchase_fee_import"] = 0.04
    registry.activate_remote_registry(remote.parse_remote_registry(candidate))
    assert registry.get_supplier_tariff("custom", NOW) == original
    assert registry.get_supplier_tariff("anwb_energie", NOW).purchase_fee_import == 0.018
    assert registry.get_supplier_tariff("anwb_energie", date(2026, 10, 1)).purchase_fee_import == 0.04
    custom = replace(original, purchase_fee_electricity=0.09, purchase_fee_import=0.09)
    assert registry.get_supplier_tariff("anwb_energie", NOW, custom=custom).purchase_fee_import == 0.09


@pytest.mark.parametrize("minutes", [15, 60])
@pytest.mark.parametrize("day", ["2026-10-01", "2026-03-29", "2026-10-25"])
def test_remote_per_entry_boundary_and_dst(minutes, day):
    zone = ZoneInfo("Europe/Amsterdam")
    boundary = datetime.fromisoformat(day).replace(tzinfo=zone)
    candidate = payload()
    first = candidate["suppliers"]["anwb_energie"]["tariffs"][0]
    first["valid_until"] = (boundary.date() - timedelta(days=1)).isoformat()
    second = {**first, "valid_from": day, "valid_until": None, "purchase_fee_import": 0.04}
    candidate["suppliers"]["anwb_energie"]["tariffs"].append(second)
    registry.activate_remote_registry(remote.parse_remote_registry(candidate))
    start = boundary.astimezone(timezone.utc) - timedelta(minutes=minutes)
    prices = [PriceEntry((start + timedelta(minutes=i * minutes)).astimezone(zone), 0.1)
              for i in range(28 * 60 // minutes)]
    # Combined provider data must keep both repeated DST hours ordered in absolute time.
    result = ProviderResult("test", prices[:1], prices[1:])
    profile = registry.get_supplier_tariff("anwb_energie", prices[0].time)
    attrs = build_all_in_price_attributes_for_supplier(result.prices, 0.1, profile, 0.21)
    assert attrs[0]["price"] == pytest.approx(0.239)
    assert all(item["price"] == pytest.approx(0.261) for item in attrs[1:])
    times = [datetime.fromisoformat(item["time"]).timestamp() for item in attrs]
    assert times == sorted(set(times))
    assert all(set(item) == {"time", "price"} for item in attrs)


def test_duplicate_json_keys_rejected():
    with pytest.raises(ValueError):
        remote.decode_registry(b'{"revision": 1, "revision": 2}')


async def test_confirmed_store_detects_silent_write_failure():
    store = Store({"old": True})
    store.async_save = Mock(side_effect=lambda data: asyncio.sleep(0))
    confirmed = remote.ConfirmedStore(store, (RuntimeError,))
    with pytest.raises(OSError):
        await confirmed.async_save({"new": True})


async def test_disabled_mode_never_downloads_or_activates_cache():
    manager, session, _ = setup(4)
    manager.enabled = False
    await manager.async_load()
    assert not await manager.async_check()
    assert manager.active.revision == 1
    session.get.assert_not_called()


async def test_newer_bundle_wins_over_cache():
    manager, session, _ = setup(4)
    manager.active = replace(registry.load_registry(), revision=5)
    await manager.async_load()
    assert manager.active.revision == 5
    assert manager.payload is None
    session.get.assert_not_called()


async def test_missing_bundle_does_not_hide_valid_revision_one_cache(monkeypatch):
    empty = registry.SupplierRegistry(1, {}, revision=0)
    monkeypatch.setattr(registry, "load_registry", lambda: empty)
    manager, session, _ = setup(1)
    await manager.async_load()
    assert manager.active.revision == 1
    assert registry.get_supplier_tariff("tibber", NOW).registry_source == "remote"
    session.get.assert_not_called()


async def test_subscriptions_share_background_task_and_cancel_last_unload():
    manager, _, _ = setup()
    task = Mock()
    def create_task(coro, name):
        coro.close()
        return task
    hass = Mock()
    hass.async_create_background_task.side_effect = create_task
    first, second = Mock(), Mock()
    unsubscribe_first = remote.async_subscribe(hass, manager, first)
    unsubscribe_second = remote.async_subscribe(hass, manager, second)
    hass.async_create_background_task.assert_called_once()
    unsubscribe_first()
    task.cancel.assert_not_called()
    unsubscribe_second()
    task.cancel.assert_called_once()
    assert manager.task is None
    assert manager.listeners == set()


async def test_invalid_period_update_does_not_destroy_good_cache():
    manager, session, store = setup(4, 5)
    candidate = payload(5)
    records = candidate["suppliers"]["tibber"]["tariffs"]
    records[0]["valid_until"] = None
    session.get.return_value = Response(candidate)
    await manager.async_load()
    assert not await manager.async_check()
    assert store.data["registry"] == payload(4)
    assert manager.active.revision == 4


async def test_unreadable_cache_and_storage_wrapper_errors():
    manager, _, store = setup()
    async def failed_load():
        raise OSError("unreadable")
    store.async_load = failed_load
    await manager.async_load()
    assert manager.active.revision == 1
    async def incompatible_load():
        raise RuntimeError("incompatible storage version")
    store.async_load = incompatible_load
    confirmed = remote.ConfirmedStore(store, (RuntimeError,))
    with pytest.raises(OSError):
        await confirmed.async_load()


def test_published_schema_covers_runtime_fields():
    schema = json.loads((ROOT / "registry/supplier_tariffs.schema.json").read_text())
    suppliers = schema["properties"]["suppliers"]
    assert set(suppliers["required"]) == remote.REQUIRED_SUPPLIERS
    tariff = suppliers["additionalProperties"]["properties"]["tariffs"]["items"]
    assert set(tariff["required"]) == remote.TARIFF_FIELDS
    assert tariff["properties"]["purchase_fee_export"]["minimum"] == -remote.MAX_KWH_FEE
    assert tariff["properties"]["fixed_monthly_fee_electricity"]["maximum"] == remote.MAX_MONTHLY_FEE


NUMERIC_RULES = [
    ("purchase_fee_import", 0, remote.MAX_KWH_FEE),
    ("purchase_fee_export", -remote.MAX_KWH_FEE, remote.MAX_KWH_FEE),
    ("fixed_monthly_fee_electricity", 0, remote.MAX_MONTHLY_FEE),
    ("feed_in_fee", -remote.MAX_KWH_FEE, remote.MAX_KWH_FEE),
    ("imbalance_fee", -remote.MAX_KWH_FEE, remote.MAX_KWH_FEE),
]


@pytest.mark.parametrize("field,minimum,maximum", NUMERIC_RULES)
def test_numeric_schema_runtime_alignment(field, minimum, maximum):
    schema = json.loads((ROOT / "registry/supplier_tariffs.schema.json").read_text())
    rule = schema["properties"]["suppliers"]["additionalProperties"]["properties"]["tariffs"]["items"]["properties"][field]
    assert rule["minimum"] == minimum
    assert rule["maximum"] == maximum
    assert "exclusiveMinimum" not in rule and "exclusiveMaximum" not in rule
    assert rule["type"] == (["number", "null"] if field == "imbalance_fee" else "number")
    assert remote.NUMERIC_TARIFF_BOUNDS[field] == (minimum, maximum)


@pytest.mark.parametrize("field,minimum,maximum", NUMERIC_RULES)
def test_numeric_endpoints_and_realistic_values_accepted(field, minimum, maximum):
    values = [minimum, maximum, 0, 0.01299]
    if minimum < 0:
        values.append(-0.01299)
    if field == "imbalance_fee":
        values.append(None)
    for value in values:
        candidate = payload()
        candidate["suppliers"]["anwb_energie"]["tariffs"][0][field] = value
        profile = remote.parse_remote_registry(candidate).suppliers["anwb_energie"][0]
        assert getattr(profile, field) == value


@pytest.mark.parametrize("field,minimum,maximum", NUMERIC_RULES)
def test_numeric_out_of_bounds_rejected_before_normalization(field, minimum, maximum, monkeypatch):
    monkeypatch.setattr(validation, "parse_registry", Mock(side_effect=AssertionError("Reached normalization")))
    for value in (minimum - 0.000001, maximum + 0.000001, 10**400, -(10**400)):
        candidate = payload()
        candidate["suppliers"]["anwb_energie"]["tariffs"][0][field] = value
        with pytest.raises(ValueError, match=f"{field} outside safety bounds"):
            remote.parse_remote_registry(candidate)


@pytest.mark.parametrize("field,minimum,maximum", NUMERIC_RULES)
@pytest.mark.parametrize("value", [True, False, "0.02", [], {}, float("nan"), float("inf"), -float("inf")])
def test_numeric_invalid_types_and_nonfinite_rejected_before_normalization(
    field, minimum, maximum, value, monkeypatch
):
    monkeypatch.setattr(validation, "parse_registry", Mock(side_effect=AssertionError("Reached normalization")))
    candidate = payload()
    candidate["suppliers"]["anwb_energie"]["tariffs"][0][field] = value
    with pytest.raises(ValueError, match=field):
        remote.parse_remote_registry(candidate)


@pytest.mark.parametrize("field", [field for field, _, _ in NUMERIC_RULES if field != "imbalance_fee"])
def test_null_rejected_for_nonnullable_numeric_fields(field):
    candidate = payload()
    candidate["suppliers"]["anwb_energie"]["tariffs"][0][field] = None
    with pytest.raises(ValueError, match=field):
        remote.parse_remote_registry(candidate)
