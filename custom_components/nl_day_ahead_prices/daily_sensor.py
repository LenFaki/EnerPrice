"""Bounded forecast summaries, separate from price and savings semantics."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


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
