"""Delivery consent, durability, calendar semantics and HA lifecycle boundaries."""

import asyncio
import importlib.util
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest

from custom_components.nl_day_ahead_prices.const import (
    CONF_DAILY_ADVISOR_ENABLED,
    CONF_DAILY_NOTIFY_CONSENT,
    CONF_DAILY_NOTIFY_ENABLED,
    CONF_DAILY_NOTIFY_TARGET,
    CONF_DAILY_NOTIFY_TIME,
    CONF_DAILY_RETENTION_DAYS,
    DAILY_CONSENT_TARGET,
)
from custom_components.nl_day_ahead_prices.daily_storage import MAX_BYTES, DailyAdvisorStore, _load, _save
from custom_components.nl_day_ahead_prices.daily_tracking import empty_history
from custom_components.nl_day_ahead_prices.solar_forecast import AMSTERDAM
from tests.test_daily_forecast import ROOT, load_adapter

TARGET = "notify.mobile_app_phone"


class MemoryStore:
    def __init__(self):
        self.data = None
        self.fail = False
        self.writes = []
        self.on_save = None

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, value):
        if self.fail:
            raise OSError("disk full")
        self.data = deepcopy(value)
        self.writes.append(self.data)
        if self.on_save:
            await self.on_save(value)


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    adapter = load_adapter(monkeypatch)
    adapter.clock.now = datetime(2026, 10, 12, 6, 0, tzinfo=timezone.utc)
    dt = sys.modules["homeassistant.util"].dt
    dt.as_local = lambda value: value.astimezone(ZoneInfo(adapter.hass.config.time_zone))
    module_name = "custom_components.nl_day_ahead_prices.daily_delivery"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "custom_components/nl_day_ahead_prices/daily_delivery.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    adapter.hass.config = SimpleNamespace(time_zone="Europe/Amsterdam", language="nl", path=lambda *parts: str(tmp_path.joinpath(*parts)))
    adapter.hass.is_stopping = False
    adapter.hass.services.async_services = lambda: {"notify": {"mobile_app_phone": {}, "persistent_notification": {}, "smtp_mail": {}, "notify": {}}}
    adapter.hass.services.async_call = AsyncMock()
    adapter.entry.options = {CONF_DAILY_ADVISOR_ENABLED: True, CONF_DAILY_NOTIFY_ENABLED: True, CONF_DAILY_NOTIFY_CONSENT: True,
                             CONF_DAILY_NOTIFY_TARGET: TARGET, DAILY_CONSENT_TARGET: TARGET, CONF_DAILY_NOTIFY_TIME: "08:00:00"}
    adapter.entry.async_create_background_task = lambda hass, coro, name: asyncio.create_task(coro)
    forecast = SimpleNamespace(snapshot=lambda: {"days": [], "measurements": []}, async_update_listeners=Mock())
    coordinator = SimpleNamespace(hass=adapter.hass, entry=adapter.entry, daily_forecast=forecast)
    monkeypatch.setattr(adapter.module, "daily_advice", lambda *args: {"days": [], "advice": []})
    store = MemoryStore()

    def create():
        delivery = module.DailyAdvisorDelivery(coordinator)
        delivery.store = store
        return delivery

    return SimpleNamespace(**vars(adapter), delivery_module=module, create=create, store=store, coordinator=coordinator)


async def test_claim_saved_before_call_restart_reload_same_day_no_duplicate(runtime):
    async def send(*args, **kwargs):
        assert runtime.store.data["notification_date"] == runtime.clock.now.astimezone(AMSTERDAM).date().isoformat()
        assert runtime.store.data["notification_result"] == "claimed"
    runtime.hass.services.async_call.side_effect = send
    delivery = runtime.create()
    await delivery.async_start()
    assert delivery.snapshot()["notification_state"] == "sent"
    await asyncio.gather(delivery.async_process(), delivery.async_process())
    await delivery.async_stop()
    again = runtime.create()
    await again.async_start()
    runtime.hass.services.async_call.assert_awaited_once()
    runtime.clock.now += timedelta(days=1)
    await again.async_process()
    assert runtime.hass.services.async_call.await_count == 2
    await again.async_stop()


@pytest.mark.parametrize("change,expected", [
    ({CONF_DAILY_NOTIFY_CONSENT: False}, "awaiting_consent"),
    ({CONF_DAILY_NOTIFY_ENABLED: False}, "disabled"),
    ({CONF_DAILY_ADVISOR_ENABLED: False}, "disabled"),
    ({DAILY_CONSENT_TARGET: None}, "awaiting_consent"),
    ({CONF_DAILY_NOTIFY_TARGET: "notify.smtp_mail", DAILY_CONSENT_TARGET: "notify.smtp_mail"}, "invalid_target"),
    ({CONF_DAILY_NOTIFY_TARGET: "notify.mobile_app_missing", DAILY_CONSENT_TARGET: "notify.mobile_app_missing"}, "invalid_target"),
])
async def test_consent_gate_never_sends_implicitly(runtime, change, expected):
    runtime.entry.options.update(change)
    delivery = runtime.create()
    await delivery.async_start()
    assert delivery.snapshot()["notification_state"] == expected
    runtime.hass.services.async_call.assert_not_awaited()
    await delivery.async_stop()


@pytest.mark.parametrize("failure", ["write", "corrupt", "future_schema", "shutdown"])
async def test_fail_closed_storage_or_shutdown(runtime, failure):
    if failure == "write":
        runtime.store.fail = True
    elif failure == "corrupt":
        runtime.store.data = {"schema": 1, "days": [None]}
    elif failure == "future_schema":
        runtime.store.data = {"schema": 99, "days": []}
    else:
        runtime.hass.is_stopping = True
    delivery = runtime.create()
    await delivery.async_start()
    runtime.hass.services.async_call.assert_not_awaited()
    await delivery.async_stop()


@pytest.mark.parametrize("change", ["stop", "revoke", "shutdown", "target"])
async def test_no_send_after_consent_or_lifecycle_changes_while_claim_saves(runtime, change):
    delivery = runtime.create()
    async def after_save(value):
        if value["notification_result"] != "claimed":
            return
        if change == "stop":
            delivery._stopped = True
        elif change == "shutdown":
            runtime.hass.is_stopping = True
        elif change == "target":
            runtime.entry.options[CONF_DAILY_NOTIFY_TARGET] = "notify.persistent_notification"
        else:
            runtime.entry.options[CONF_DAILY_NOTIFY_CONSENT] = False
    runtime.store.on_save = after_save
    await delivery.async_start()
    runtime.hass.services.async_call.assert_not_awaited()
    assert runtime.store.data["notification_result"] == "claimed"
    await delivery.async_stop()


async def test_service_timeout_no_retry_even_after_restart(runtime):
    runtime.hass.services.async_call.side_effect = TimeoutError
    delivery = runtime.create()
    await delivery.async_start()
    assert delivery.snapshot()["notification_state"] == "failed"
    await delivery.async_stop()
    replacement = runtime.create()
    await replacement.async_start()
    runtime.hass.services.async_call.assert_awaited_once()
    await replacement.async_stop()


async def test_crash_after_claim_is_not_replayed(runtime):
    runtime.store.data = {**empty_history(), "notification_date": "2026-10-12", "notification_result": "claimed"}
    delivery = runtime.create()
    await delivery.async_start()
    runtime.hass.services.async_call.assert_not_awaited()
    assert delivery.snapshot()["notification_state"] == "claimed"
    await delivery.async_stop()


@pytest.mark.parametrize("zone,hour", [("Europe/Amsterdam", 6), ("America/New_York", 12), ("Asia/Tokyo", 23)])
async def test_schedule_uses_ha_local_timezone(runtime, zone, hour):
    runtime.hass.config.time_zone = zone
    runtime.clock.now = datetime(2026, 10, 12, hour, tzinfo=timezone.utc)
    delivery = runtime.create()
    await delivery.async_start()
    runtime.hass.services.async_call.assert_awaited_once()
    assert runtime.store.data["notification_date"] == runtime.clock.now.astimezone(ZoneInfo(zone)).date().isoformat()
    await delivery.async_stop()


def test_dst_nonexistent_time_skipped_repeated_hour_shares_date(runtime):
    due = runtime.delivery_module.due_date
    spring = datetime(2026, 3, 29, 3, 0, tzinfo=AMSTERDAM)
    assert due(spring, "02:45:00", None) is None
    first = datetime(2026, 10, 25, 2, 30, tzinfo=AMSTERDAM, fold=0)
    second = first.replace(fold=1)
    assert due(first, "02:30:00", None) == "2026-10-25"
    assert due(second, "02:30:00", "2026-10-25") is None
    assert due(first, "02:30:00", "2026-10-26") is None
    assert due(first.replace(hour=3), "02:30:00", None) is None


def test_legacy_options_default_off_new_preference_on_never_consented(runtime):
    module = runtime.delivery_module
    assert module.notification_preference({}) is True
    assert module.notification_preference({CONF_DAILY_ADVISOR_ENABLED: True}) is False
    assert module.notification_preference({"daily_forecast_entities": []}) is False
    assert module.delivery_gate({CONF_DAILY_ADVISOR_ENABLED: True, CONF_DAILY_NOTIFY_ENABLED: True}, [TARGET]) == "awaiting_consent"
    assert module.notification_targets(runtime.hass) == [TARGET, "notify.persistent_notification"]


@pytest.mark.parametrize("key,value,error", [(CONF_DAILY_RETENTION_DAYS, 0, "daily_invalid_retention"),
                                            (CONF_DAILY_RETENTION_DAYS, 91, "daily_invalid_retention"),
                                            (CONF_DAILY_RETENTION_DAYS, True, "daily_invalid_retention"),
                                            (CONF_DAILY_NOTIFY_TIME, "08:00:01", "daily_invalid_time"),
                                            (CONF_DAILY_NOTIFY_TIME, "08:00+02:00", "daily_invalid_time")])
def test_options_validation(runtime, key, value, error):
    assert runtime.delivery_module.validate_delivery_options(runtime.hass, {key: value}) == {key: error}


@pytest.mark.parametrize("language,unknown", [("nl", "onbekend"), ("en", "unknown")])
def test_briefing_unknowns_and_max_three_advice_records(runtime, language, unknown):
    item = {"start": runtime.clock.now.isoformat(), "end": (runtime.clock.now + timedelta(hours=1)).isoformat(), "reason": "Test reason"}
    text = runtime.delivery_module.briefing({"days": [], "advice": [item] * 8}, runtime.clock.now, language)
    assert unknown in text
    assert text.count("Test reason") == 3
    assert "0.00 kWh" not in text


async def test_atomic_storage_executor_and_corruption_persists(tmp_path):
    hass = SimpleNamespace(config=SimpleNamespace(path=lambda *parts: str(tmp_path.joinpath(*parts))),
                           async_add_executor_job=AsyncMock(side_effect=lambda fn, *args: fn(*args)))
    store = DailyAdvisorStore(hass, "daily.json")
    assert await store.async_load() is None
    await store.async_save(empty_history())
    assert await store.async_load() == empty_history()
    hass.async_add_executor_job.assert_awaited()
    path = tmp_path / ".storage/daily.json"
    await asyncio.to_thread(path.write_text, "broken json")
    for _ in range(2):
        with pytest.raises(ValueError):
            await store.async_load()
    assert await asyncio.to_thread(path.read_text) == "broken json"


def test_storage_write_failure_preserves_previous_claim(tmp_path, monkeypatch):
    path = str(tmp_path / "daily.json")
    claim = {**empty_history(), "notification_date": "2026-10-12", "notification_result": "claimed"}
    _save(path, claim)
    monkeypatch.setattr("custom_components.nl_day_ahead_prices.daily_storage.os.replace", Mock(side_effect=OSError("write failed")))
    with pytest.raises(OSError):
        _save(path, empty_history())
    assert _load(path) == claim
    assert list(tmp_path.iterdir()) == [tmp_path / "daily.json"]


def test_storage_rejects_oversized_and_nonfinite_json(tmp_path):
    path = tmp_path / "daily.json"
    with pytest.raises(ValueError):
        _save(str(path), {"oversized": "a" * MAX_BYTES})
    with pytest.raises(ValueError):
        _save(str(path), {"value": float("nan")})
    path.write_text('{"value": NaN}')
    with pytest.raises(ValueError):
        _load(str(path))


@pytest.mark.parametrize("raw", ["null", '{"schema":1,"days":[]}', '{"schema":1,"days":[],"notification_date":null,"notification_result":"sent"}'])
async def test_corrupt_claim_on_disk_never_resets_on_repeated_restarts(runtime, tmp_path, raw):
    from pathlib import Path

    runtime.hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))
    store = DailyAdvisorStore(runtime.hass, "claim.json")
    path = Path(store.path)
    await asyncio.to_thread(path.parent.mkdir, exist_ok=True)
    await asyncio.to_thread(path.write_text, raw)
    for _ in range(2):
        delivery = runtime.create()
        delivery.store = store
        await delivery.async_start()
        assert delivery.snapshot()["notification_state"] == "storage_error"
        await delivery.async_stop()
    runtime.hass.services.async_call.assert_not_awaited()
    assert await asyncio.to_thread(path.read_text) == raw


async def test_pending_tick_coalesces_and_unload_removes_clock(runtime):
    runtime.entry.options[CONF_DAILY_NOTIFY_ENABLED] = False
    delivery = runtime.create()
    await delivery.async_start()
    remove = delivery._remove_clock
    delivery._tick(None)
    task = delivery._task
    delivery._tick(None)
    assert delivery._task is task
    await delivery.async_stop()
    assert task.done()
    remove.assert_called_once()
    delivery._tick(None)
    assert delivery._task is task


async def test_options_flow_consent_binding_revocation_and_unrelated_values(runtime, monkeypatch):
    from tests.test_registry_ux import _load_config_flow_for_test, _smart_setup_flow

    module = _load_config_flow_for_test(monkeypatch)
    existing = {**runtime.entry.options, "selected_supplier": "custom", "smart_flexible_load_power_w": 2500}
    flow = _smart_setup_flow(module, existing)
    flow.hass = runtime.hass
    flow.config_entry = SimpleNamespace(data={}, options=existing)
    form = await flow.async_step_daily_advisor()
    consent = next(key for key in form["data_schema"] if key.key == CONF_DAILY_NOTIFY_CONSENT)
    assert consent.default is False
    result = await flow.async_step_daily_advisor({CONF_DAILY_NOTIFY_CONSENT: True, CONF_DAILY_NOTIFY_TARGET: "notify.persistent_notification"})
    assert result["data"][DAILY_CONSENT_TARGET] == "notify.persistent_notification"
    assert result["data"]["smart_flexible_load_power_w"] == 2500
    result = await flow.async_step_daily_advisor({CONF_DAILY_NOTIFY_CONSENT: False})
    assert result["data"][DAILY_CONSENT_TARGET] is None
    result = await flow.async_step_daily_advisor({CONF_DAILY_NOTIFY_TARGET: "notify.persistent_notification"})
    assert result["data"][DAILY_CONSENT_TARGET] is None


@pytest.mark.parametrize("existing,expected", [({}, True), ({CONF_DAILY_ADVISOR_ENABLED: True}, False),
                                             ({"daily_forecast_entities": []}, False)])
async def test_options_flow_safe_new_and_existing_defaults(runtime, monkeypatch, existing, expected):
    from tests.test_registry_ux import _load_config_flow_for_test, _smart_setup_flow

    module = _load_config_flow_for_test(monkeypatch)
    flow = _smart_setup_flow(module, {**existing, CONF_DAILY_ADVISOR_ENABLED: True})
    flow.hass = runtime.hass
    flow.config_entry = SimpleNamespace(data={}, options=existing)
    form = await flow.async_step_daily_advisor()
    preference = next(key for key in form["data_schema"] if key.key == CONF_DAILY_NOTIFY_ENABLED)
    assert preference.default is expected


@pytest.mark.parametrize("language", ["nl", "en"])
def test_performance_dashboard_real_ids_yaml_jinja_and_unknowns(language):
    from jinja2 import Environment, StrictUndefined
    from yaml import safe_load

    from custom_components.nl_day_ahead_prices.dashboard import generate_dashboard_yaml

    ids = {"daily_energy_advice": "sensor.advice", "daily_notification_status": "sensor.renamed_notify", "daily_energy_performance": "sensor.renamed_check"}
    doc = safe_load(generate_dashboard_yaml("compact", language=language, include_price_advisor=False,
                                           include_daily_advisor=True, use_apexcharts=False, entity_ids=ids))
    cards = doc["views"][0]["sections"][-1]["cards"]
    assert any(card.get("entity") == "sensor.renamed_notify" for card in cards)
    assert any(card.get("entity") == "sensor.renamed_check" for card in cards)
    template = [card["content"] for card in cards if card["type"] == "markdown"][-1]
    data = {"date": "2026-10-12", "potential_savings_eur": None,
            "metrics": {"solar_energy": {"predicted_kwh": 5, "actual_kwh": None, "error_kwh": None}}}
    env = Environment(undefined=StrictUndefined)
    env.globals["state_attr"] = lambda entity, attr: data.get(attr)
    rendered = env.from_string(template).render()
    assert "2026-10-12" in rendered
    assert ("onbekend" if language == "nl" else "unknown") in rendered
    assert "None" not in rendered
    assert "0.0" not in rendered


def test_actual_counter_adapter_uses_reports_but_power_and_forecasts_do_not(runtime):
    from tests.test_daily_forecast import state

    now = runtime.clock.now
    counter = state("100", updated=now - timedelta(days=1), state_class="total_increasing")
    counter.last_reported = now
    runtime.states["sensor.import"] = counter
    runtime.entry.options["daily_import_energy_entity"] = ["sensor.import"]
    forecast = runtime.module.DailyForecastCoordinator(runtime.hass, runtime.entry)
    value = next(row for row in forecast.snapshot()["measurements"] if row["role"] == "import_energy")
    assert value["value"] == 100
    assert value["observed_at"] == now.isoformat()
    counter.attributes["restored"] = True
    assert next(row for row in forecast.snapshot()["measurements"] if row["role"] == "import_energy")["value"] is None


def test_new_entities_stable_ids_and_latest_summary_only(runtime, monkeypatch):
    from types import ModuleType

    class Entity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

    modules = {
        "homeassistant.components.sensor": {"SensorDeviceClass": SimpleNamespace(ENUM="enum", TIMESTAMP="timestamp"), "SensorEntity": type("Sensor", (), {})},
        "homeassistant.const": {"EntityCategory": SimpleNamespace(DIAGNOSTIC="diagnostic"), "UnitOfEnergy": SimpleNamespace(KILO_WATT_HOUR="kWh")},
    }
    for name, attrs in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys.modules["homeassistant.helpers.update_coordinator"], "CoordinatorEntity", Entity, raising=False)
    name = "custom_components.nl_day_ahead_prices._daily_delivery_sensor_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "custom_components/nl_day_ahead_prices/daily_sensor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime.coordinator.daily_delivery = runtime.create()
    entities = module.daily_advice_entities(runtime.coordinator, runtime.entry)
    assert [entity._attr_unique_id for entity in entities] == ["test_daily_energy_advice", "test_best_consumption_window",
                                                            "test_daily_notification_status", "test_daily_energy_performance"]
    for entity in entities[-2:]:
        assert entity.available
        assert "days" not in entity.extra_state_attributes
        assert TARGET not in str(entity.extra_state_attributes)
    assert entities[-1].native_value == "storage_error"
