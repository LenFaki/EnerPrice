"""Bounded forecast summaries, separate from price and savings semantics."""

import asyncio
import logging
from datetime import datetime, timezone
from functools import partial

from homeassistant.util import dt as dt_util

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ADVICE_LANGUAGE, CONF_FLEXIBLE_LOAD_POWER_W, DOMAIN

_LOGGER = logging.getLogger(__name__)


class _AsyncDailyAdvice:
    """Compute expensive advice off the HA event loop and publish only fresh results."""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.result = None
        self.sensors = set()
        self.revision = 0
        self.task = None

    def request(self):
        self.revision += 1
        if self.task is None or self.task.done():
            self.task = self.coordinator.hass.async_create_task(self._run())

    async def _run(self):
        from .daily_advisor import advisor_context
        from .models import PriceEntry
        from .sensor import _cached_all_in_entries
        from .smart_energy import build_smart_energy_advice

        while self.sensors:
            revision = self.revision
            coordinator = self.coordinator
            options = {**coordinator.entry.data, **coordinator.entry.options}
            language = options.get(CONF_ADVICE_LANGUAGE, "auto")
            if language == "auto":
                language = coordinator.hass.config.language
            now = dt_util.utcnow()
            prices = (_cached_all_in_entries(coordinator, coordinator.data, coordinator.entry)
                      if coordinator.data else [])
            utc_prices = [PriceEntry(item.time.astimezone(timezone.utc), item.price) for item in prices]
            context = advisor_context(coordinator)
            calculate = partial(
                build_smart_energy_advice, electricity_price=None,
                future_prices=utc_prices, now=now,
                flexible_load_power_w=options.get(CONF_FLEXIBLE_LOAD_POWER_W),
                language=language, daily_context=context,
            )
            try:
                advice = await coordinator.hass.async_add_executor_job(calculate)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("EnerPrice Daily Advisor background calculation failed")
                if revision == self.revision:
                    return
                continue
            if revision == self.revision:
                self.result = advice["daily_advisor"]
                for sensor in tuple(self.sensors):
                    sensor.async_write_ha_state()
                return
            # Inputs changed during calculation: discard stale output and rerun.

    def add(self, sensor):
        self.sensors.add(sensor)
        self.request()

    def remove(self, sensor):
        self.sensors.discard(sensor)
        if not self.sensors and self.task and not self.task.done():
            self.task.cancel()




def daily_advice_entities(coordinator, entry):
    entities = [DailyAdviceSensor(coordinator, entry, key) for key in ("daily_energy_advice", "best_consumption_window")]
    if getattr(coordinator, "daily_delivery", None) is not None:
        entities.extend(DailyDeliverySensor(coordinator, entry, key) for key in ("daily_notification_status", "daily_energy_performance"))
    return entities


class DailyDeliverySensor(CoordinatorEntity, SensorEntity):
    """Small latest-day summaries, never raw history or inferred realized savings."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator, entry, key):
        super().__init__(coordinator.daily_forecast)
        self.delivery = coordinator.daily_delivery
        self.key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": "EnerPrice", "manufacturer": "EnerPrice"}
        self._attr_options = (["loading", "disabled", "awaiting_consent", "invalid_target", "scheduled", "claimed", "sent", "failed", "storage_error"]
                              if key == "daily_notification_status" else ["collecting", "comparable", "incomplete", "storage_error"])

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        result = self.delivery.snapshot()
        if self.key == "daily_notification_status":
            return result["notification_state"]
        return result["performance"]["state"] if result["storage_state"] == "ready" else "storage_error"

    @property
    def extra_state_attributes(self):
        result = self.delivery.snapshot()
        if self.key == "daily_energy_performance":
            return {**result["performance"], "retention_days": result["retention_days"], "stored_days": result["stored_days"],
                    "storage_state": result["storage_state"]}
        return {key: value for key, value in result.items() if key not in ("performance", "notification_state")}


class DailyAdviceSensor(CoordinatorEntity, SensorEntity):
    """Existing advisor output, refreshed by both prices and forecast measurements."""

    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset({"chart"})

    def __init__(self, coordinator, entry, key):
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": "EnerPrice", "manufacturer": "EnerPrice"}
        self._attr_device_class = SensorDeviceClass.TIMESTAMP if key == "best_consumption_window" else SensorDeviceClass.ENUM
        if key == "daily_energy_advice":
            self._attr_options = ["ready", "unavailable"]
        worker = getattr(coordinator, "_daily_advice_worker", None)
        if worker is None:
            worker = coordinator._daily_advice_worker = _AsyncDailyAdvice(coordinator)
        self._worker = worker

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._worker.add(self)
        self.async_on_remove(lambda: self._worker.remove(self))
        self.async_on_remove(self.coordinator.daily_forecast.async_add_listener(self._worker.request))

    def _handle_coordinator_update(self):
        """Schedule a new result instead of blocking the sensor state callback."""
        self._worker.request()

    def _advice(self):
        return self._worker.result or {"state": "unavailable", "best_window": None}

    @property
    def available(self):
        return self.key == "daily_energy_advice" or self.native_value is not None

    @property
    def native_value(self):
        result = self._advice()
        if self.key == "daily_energy_advice":
            return result["state"]
        best = result["best_window"]
        return datetime.fromisoformat(best["start"]) if best else None

    @property
    def extra_state_attributes(self):
        result = self._advice()
        if self.key == "best_consumption_window":
            return result["best_window"] or {}
        return {key: value for key, value in result.items() if key != "state"}


def daily_forecast_entities(coordinator, entry):
    return [DailyForecastSensor(coordinator, entry, key, offset) for key, offset in (
        ("solar_forecast_today", 0), ("solar_forecast_tomorrow", 1), ("daily_forecast_quality", None),
    )]


class DailyForecastSensor(CoordinatorEntity, SensorEntity):
    """Read forecast data even when market prices are unavailable."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, key, offset):
        super().__init__(coordinator)
        self.offset = offset
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": "EnerPrice", "manufacturer": "EnerPrice"}
        if offset is None:
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ["partial", "unavailable"]
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        else:
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            self._attr_suggested_display_precision = 2
        # Forecasts are not measured energy: intentionally no total/total_increasing state class.

    @property
    def available(self):
        return self.offset is None or self.native_value is not None

    @property
    def native_value(self):
        if self.offset is None:
            return self.coordinator.data["quality"]
        return self.coordinator.data["days"][self.offset]["energy_kwh"]

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        return {"source": data["source"], "kind": "forecast", "days": data["days"],
                "issues": data["issues"], "series_status": data["series_status"],
                "installation_count": data["installation_count"], "freshness_basis": data["freshness_basis"],
                "solar_surplus_kwh": None}
