"""Opt-in HA briefing delivery and bounded evidence lifecycle; no device control."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_utc_time_change
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ADVICE_LANGUAGE,
    CONF_DAILY_ADVISOR_ENABLED,
    CONF_DAILY_NOTIFY_CONSENT,
    CONF_DAILY_NOTIFY_ENABLED,
    CONF_DAILY_NOTIFY_TARGET,
    CONF_DAILY_NOTIFY_TIME,
    CONF_DAILY_RETENTION_DAYS,
    DAILY_CONSENT_TARGET,
    DAILY_INPUT_KEYS,
    DOMAIN,
)
from .daily_tracking import (
    MAX_RETENTION_DAYS,
    empty_history,
    performance_summary,
    restore_history,
    storage_snapshot,
    update_history,
)
from .solar_forecast import AMSTERDAM

_LOGGER = logging.getLogger(__name__)


def notification_preference(existing):
    """New setups prefer notifications; legacy setups stay off without consent."""
    return existing.get(CONF_DAILY_NOTIFY_ENABLED, not (
        existing.get(CONF_DAILY_ADVISOR_ENABLED, False) or any(key in existing for key in DAILY_INPUT_KEYS)
    ))


def notification_targets(hass):
    """Explicit non-email HA targets only; never use the broadcast notify alias."""
    return sorted(f"notify.{name}" for name in hass.services.async_services().get("notify", {})
                  if name == "persistent_notification" or name.startswith("mobile_app_"))


def validate_delivery_options(hass, options):
    errors = {}
    retention = options.get(CONF_DAILY_RETENTION_DAYS, 30)
    if type(retention) not in (int, float) or not 1 <= retention <= MAX_RETENTION_DAYS or retention % 1:
        errors[CONF_DAILY_RETENTION_DAYS] = "daily_invalid_retention"
    try:
        value = options.get(CONF_DAILY_NOTIFY_TIME, "08:00:00")
        parsed = time.fromisoformat(value)
        if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
            raise ValueError
    except (ValueError, TypeError):
        errors[CONF_DAILY_NOTIFY_TIME] = "daily_invalid_time"
    if options.get(CONF_DAILY_NOTIFY_CONSENT) and options.get(CONF_DAILY_NOTIFY_ENABLED):
        if options.get(CONF_DAILY_NOTIFY_TARGET) not in notification_targets(hass):
            errors[CONF_DAILY_NOTIFY_TARGET] = "daily_invalid_notify_target"
    return errors


def delivery_gate(options, targets):
    if not options.get(CONF_DAILY_ADVISOR_ENABLED) or not options.get(CONF_DAILY_NOTIFY_ENABLED, False):
        return "disabled"
    target = options.get(CONF_DAILY_NOTIFY_TARGET)
    if options.get(CONF_DAILY_NOTIFY_CONSENT) is not True or not target or options.get(DAILY_CONSENT_TARGET) != target:
        return "awaiting_consent"
    if target not in targets:
        return "invalid_target"
    return "scheduled"


def due_date(now, send_time, watermark):
    """Local calendar gate with a 30-minute catch-up window and monotonic claim.

    Nonexistent DST times are skipped; repeated times share the same claim.
    Moving the clock/timezone backwards never replays an earlier date.
    """
    clock = time.fromisoformat(send_time)
    if clock.tzinfo is not None:
        return None
    candidate = now.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
    roundtrip = candidate.astimezone(timezone.utc).astimezone(now.tzinfo)
    if roundtrip.replace(tzinfo=None) != candidate.replace(tzinfo=None):
        return None
    elapsed = (now.hour * 60 + now.minute) - (clock.hour * 60 + clock.minute)
    day = now.date().isoformat()
    return day if 0 <= elapsed < 30 and (watermark is None or day > watermark) else None


def briefing(advice, now, language):
    """Plain text only, maximum three existing advice records, no new price model."""
    nl = language.startswith("nl")
    unknown = "onbekend" if nl else "unknown"
    today = now.astimezone(AMSTERDAM).date().isoformat()
    day = next((row for row in advice.get("days", []) if row["date"] == today), {})
    solar = day.get("solar_energy_kwh")
    lines = [f"{'Verwachte zonneopwek' if nl else 'Expected solar production'}: {f'{solar:.2f} kWh' if solar is not None else unknown}."]
    window = day.get("window")

    def period(item):
        start = datetime.fromisoformat(item["start"]).astimezone(now.tzinfo)
        end = datetime.fromisoformat(item["end"]).astimezone(now.tzinfo)
        return f"{start:%d-%m %H:%M %z} - {end:%H:%M %z}"

    lines.append(f"{'Beste gebruiksvenster vandaag' if nl else 'Best consumption window today'}: {period(window) if window else unknown}.")
    for item in advice.get("advice", [])[:3]:
        lines.append(f"- {period(item)}: {item['reason']}")
    lines.append("Zonneadvies is voorwaardelijk; besparing is niet aangetoond." if nl else "Solar advice is conditional; savings are not demonstrated.")
    return "\n".join(lines)[:3500]


class DailyAdvisorDelivery:
    """Durable at-most-once attempts, async storage, coalesced one-minute sampling."""

    def __init__(self, coordinator):
        from .daily_storage import DailyAdvisorStore

        self.coordinator = coordinator
        self.hass = coordinator.hass
        self.entry = coordinator.entry
        self.store = DailyAdvisorStore(self.hass, f"{DOMAIN}_daily_advisor_{self.entry.entry_id}.json")
        self.history = empty_history()
        self.status = "loading"
        self._loaded = False
        self._stopped = False
        self._remove_clock = None
        self._task = None
        self.start_task = None
        self._lock = asyncio.Lock()
        self._last_save = None

    @property
    def options(self):
        return {**self.entry.data, **self.entry.options}

    def snapshot(self):
        gate = delivery_gate(self.options, notification_targets(self.hass))
        state = self.status if gate == "scheduled" else gate
        result = performance_summary(self.history, dt_util.utcnow().astimezone(AMSTERDAM).date())
        return {"notification_state": state, "notification_date": self.history["notification_date"],
                "notification_result": self.history["notification_result"], "send_time": self.options.get(CONF_DAILY_NOTIFY_TIME, "08:00:00"),
                "time_zone": self.hass.config.time_zone, "retention_days": self.options.get(CONF_DAILY_RETENTION_DAYS, 30),
                "stored_days": len(self.history["days"]), "storage_state": "ready" if self._loaded else "unavailable",
                "performance": result}

    async def async_start(self):
        try:
            raw = await self.store.async_load()
            self.history = restore_history(raw, dt_util.utcnow().astimezone(AMSTERDAM).date(), self.options.get(CONF_DAILY_RETENTION_DAYS, 30))
            self._loaded = True
            self.status = "scheduled"
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, HomeAssistantError):
            self.status = "storage_error"
            _LOGGER.warning("Daily Advisor storage unavailable; notifications disabled for %s", self.entry.entry_id)
        if self._stopped:
            return
        self.coordinator.daily_forecast.async_update_listeners()
        self._remove_clock = async_track_utc_time_change(self.hass, self._tick, second=5)
        await self.async_process()

    @callback
    def _tick(self, now):
        if not self._stopped and (self._task is None or self._task.done()):
            self._task = self.entry.async_create_background_task(self.hass, self.async_process(), "EnerPrice Daily Advisor")

    async def _save(self, now):
        try:
            await self.store.async_save(storage_snapshot(self.history))
        except (OSError, ValueError, TypeError, HomeAssistantError):
            self.status = "storage_error"
            self._loaded = False
            _LOGGER.warning("Daily Advisor storage failed; notifications stopped for %s", self.entry.entry_id)
            return False
        self._last_save = now
        return True

    async def async_process(self):
        from .daily_advisor import daily_advice

        async with self._lock:
            if self._stopped or not self._loaded:
                return
            now = dt_util.utcnow()
            options = self.options
            language = options.get(CONF_ADVICE_LANGUAGE, "auto")
            if language == "auto":
                language = self.hass.config.language
            advice = daily_advice(self.coordinator, language)
            before = [row["date"] for row in self.history["days"]]
            update_history(self.history, now, self.coordinator.daily_forecast.snapshot(), advice, options.get(CONF_DAILY_RETENTION_DAYS, 30))
            if before != [row["date"] for row in self.history["days"]] or self._last_save is None or now - self._last_save >= timedelta(minutes=15):
                if not await self._save(now):
                    self.coordinator.daily_forecast.async_update_listeners()
                    return
            gate = delivery_gate(options, notification_targets(self.hass))
            self.status = gate
            # HA's configured timezone, not the price-market timezone; no ZoneInfo I/O.
            local = dt_util.as_local(now)
            day = due_date(local, options.get(CONF_DAILY_NOTIFY_TIME, "08:00:00"), self.history["notification_date"])
            if gate == "scheduled" and day and not getattr(self.hass, "is_stopping", False):
                # Persist BEFORE delivery. Crash/timeout may miss a message, never replay it.
                self.history.update(notification_date=day, notification_result="claimed")
                if await self._save(now):
                    # Options may change while storage yields. Recheck consent and target.
                    current = self.options
                    if not self._stopped and not getattr(self.hass, "is_stopping", False) and current == options and delivery_gate(current, notification_targets(self.hass)) == "scheduled":
                        try:
                            async with asyncio.timeout(15):
                                await self.hass.services.async_call("notify", current[CONF_DAILY_NOTIFY_TARGET].split(".", 1)[1],
                                                                   {"title": "EnerPrice Daily Advisor", "message": briefing(advice, local, language)}, blocking=True)
                            self.history["notification_result"] = "sent"
                        except (HomeAssistantError, TimeoutError):
                            self.history["notification_result"] = "failed"
                            _LOGGER.warning("Daily Advisor notification failed for %s; no automatic retry today", self.entry.entry_id)
                        await self._save(now)
            if self.status == "scheduled" and self.history["notification_date"] == local.date().isoformat():
                self.status = self.history["notification_result"] or "scheduled"
            self.coordinator.daily_forecast.async_update_listeners()

    async def async_stop(self):
        self._stopped = True
        if self.start_task is not None:
            await self.start_task
        if self._remove_clock:
            self._remove_clock()
            self._remove_clock = None
        # Finish a claimed delivery/write before a reloaded instance can read storage.
        if self._task is not None:
            await self._task
        async with self._lock:
            if self._loaded:
                await self._save(dt_util.utcnow())
