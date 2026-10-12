"""Opt-in local forecast coordinator for the Daily Advisor foundation."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event, async_track_utc_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DAILY_CONSUMPTION_ENTITIES,
    CONF_DAILY_FORECAST_ENTITIES,
    CONF_DAILY_GRID_POWER_ENTITY,
    CONF_DAILY_SOLAR_ENERGY_ENTITY,
    DOMAIN,
)
from .solar_forecast import (
    AMSTERDAM,
    ENERGY_UNITS,
    MAX_FORECAST_AGE,
    MAX_INSTALLATIONS,
    MAX_MEASUREMENT_AGE,
    POWER_UNITS,
    forecast_snapshot,
    forecast_source,
    numeric_state,
    parse_forecast_response,
    state_quality,
)

_LOGGER = logging.getLogger(__name__)


def selected_entities(options):
    """Return bounded, unique entity IDs for listeners and registry inspection."""
    return list(dict.fromkeys([
        *options.get(CONF_DAILY_FORECAST_ENTITIES, [])[:MAX_INSTALLATIONS * 2],
        *options.get(CONF_DAILY_CONSUMPTION_ENTITIES, [])[:MAX_INSTALLATIONS],
        *options.get(CONF_DAILY_GRID_POWER_ENTITY, [])[:1],
        *options.get(CONF_DAILY_SOLAR_ENERGY_ENTITY, [])[:1],
    ]))


def validate_daily_inputs(hass, options):
    """Check registry identity, duplicate installation roles and sensor units."""
    registry = er.async_get(hass)
    errors = {}
    seen = set()
    forecast_ids = options.get(CONF_DAILY_FORECAST_ENTITIES, [])
    if len(forecast_ids) > MAX_INSTALLATIONS * 2:
        errors[CONF_DAILY_FORECAST_ENTITIES] = "daily_too_many_entities"
    for entity_id in forecast_ids:
        source = forecast_source(entity_id, registry.async_get(entity_id))
        if source is None:
            errors[CONF_DAILY_FORECAST_ENTITIES] = "daily_invalid_forecast"
            continue
        key = (source.installation, source.day_offset)
        if key in seen:
            errors[CONF_DAILY_FORECAST_ENTITIES] = "daily_duplicate_forecast"
        seen.add(key)
    if len({key[0] for key in seen}) > MAX_INSTALLATIONS:
        errors[CONF_DAILY_FORECAST_ENTITIES] = "daily_too_many_entities"
    consumption = options.get(CONF_DAILY_CONSUMPTION_ENTITIES, [])
    if len(consumption) > MAX_INSTALLATIONS:
        errors[CONF_DAILY_CONSUMPTION_ENTITIES] = "daily_too_many_entities"
    groups = [
        (CONF_DAILY_FORECAST_ENTITIES, forecast_ids, "energy", ENERGY_UNITS),
        (CONF_DAILY_GRID_POWER_ENTITY, options.get(CONF_DAILY_GRID_POWER_ENTITY, []), "power", POWER_UNITS),
        (CONF_DAILY_SOLAR_ENERGY_ENTITY, options.get(CONF_DAILY_SOLAR_ENERGY_ENTITY, []), "energy", ENERGY_UNITS),
        (CONF_DAILY_CONSUMPTION_ENTITIES, consumption, "energy", ENERGY_UNITS),
    ]
    for field, ids, device_class, units in groups:
        if field in (CONF_DAILY_GRID_POWER_ENTITY, CONF_DAILY_SOLAR_ENERGY_ENTITY) and len(ids) > 1:
            errors[field] = "daily_single_entity"
        for entity_id in ids:
            if not entity_id:
                continue
            state = hass.states.get(entity_id)
            # Temporarily unavailable values are acceptable; metadata still must match.
            if state is None or state.attributes.get("device_class") != device_class or state.attributes.get("unit_of_measurement") not in units:
                errors[field] = "daily_invalid_unit"
    return errors


class DailyForecastCoordinator(DataUpdateCoordinator):
    """Read HA-owned data only; independent of market-provider availability."""

    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=f"{DOMAIN}_daily_forecast", update_interval=timedelta(minutes=30))
        self.entry = entry
        self.options = {**entry.data, **entry.options}
        self._remove_listener = None
        self._remove_clock = None
        self._series = []
        self._series_read_at = None
        self._series_status = "unavailable"
        self.data = self._snapshot()

    def _sources(self):
        registry = er.async_get(self.hass)
        ids = list(dict.fromkeys(self.options.get(CONF_DAILY_FORECAST_ENTITIES, [])))[:MAX_INSTALLATIONS * 2]
        return [source for entity_id in ids
                if (source := forecast_source(entity_id, registry.async_get(entity_id))) is not None]

    def snapshot(self):
        """Recheck age and calendar boundaries without requesting any data."""
        return self._snapshot()

    def _snapshot(self):
        now = dt_util.utcnow()
        sources = self._sources()
        states = {entity_id: self.hass.states.get(entity_id) for entity_id in selected_entities(self.options)}
        result = forecast_snapshot(sources, states, now)
        ids = set(self.options.get(CONF_DAILY_FORECAST_ENTITIES, []))
        if ids - {source.entity_id for source in sources}:
            result["issues"].append("unsupported_forecast_entity")
            # A missing installation must not silently turn a subtotal into a total.
            for day in result["days"]:
                day.update(energy_kwh=None, status="incomplete")
            result["quality"] = "unavailable"
        result["measurements"] = []
        groups = [
            ("grid_power", self.options.get(CONF_DAILY_GRID_POWER_ENTITY, [])[:1], "power", POWER_UNITS, "W"),
            ("solar_energy", self.options.get(CONF_DAILY_SOLAR_ENERGY_ENTITY, [])[:1], "energy", ENERGY_UNITS, "kWh"),
            ("consumption_energy", self.options.get(CONF_DAILY_CONSUMPTION_ENTITIES, [])[:MAX_INSTALLATIONS], "energy", ENERGY_UNITS, "kWh"),
        ]
        for role, entity_ids, device_class, units, unit in groups:
            for entity_id in dict.fromkeys(entity_ids):
                if not entity_id:
                    continue
                state = states.get(entity_id)
                reason = state_quality(state, now, MAX_MEASUREMENT_AGE)
                value = numeric_state(state, device_class, units) if reason is None else None
                result["measurements"].append({
                    "entity_id": entity_id, "role": role, "value": value, "unit": unit,
                    "kind": "measured" if value is not None else "unknown",
                    "status": reason or ("available" if value is not None else "invalid_value"),
                    "state_class": state.attributes.get("state_class") if state else None,
                    "last_updated": state.last_updated.isoformat() if state and state.last_updated.tzinfo else None,
                })
        # Series are response-only and never copied to sensor attributes.
        fresh = (self._series_read_at is not None
                 and timedelta(0) <= now - self._series_read_at <= MAX_FORECAST_AGE
                 and now.astimezone(AMSTERDAM).date() == self._series_read_at.astimezone(AMSTERDAM).date()
                 and not result["issues"])
        result["series"] = self._series if fresh else []
        result["series_status"] = self._series_status if fresh or not self._series else "stale"
        return result

    async def async_start(self):
        ids = selected_entities(self.options)
        if ids:
            self._remove_listener = async_track_state_change_event(self.hass, ids, self._input_changed)
        self._remove_clock = async_track_utc_time_change(self.hass, self._clock_tick, minute=[0, 15, 30, 45], second=0)

    @callback
    def _clock_tick(self, now):
        """Re-evaluate freshness and midnight rollover without delaying service polls."""
        self.data = self._snapshot()
        self.async_update_listeners()

    @callback
    def _input_changed(self, event):
        # Immediate local-only updates. The scheduled refresh reads cached series.
        if event.data.get("entity_id") in self.options.get(CONF_DAILY_FORECAST_ENTITIES, []):
            self._series = []
            self._series_status = "pending"
        self._clock_tick(None)

    async def async_stop(self):
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
        if self._remove_clock is not None:
            self._remove_clock()
            self._remove_clock = None
        await self.async_shutdown()

    async def _async_update_data(self):
        now = dt_util.utcnow()
        self._series = []
        self._series_read_at = now
        self._series_status = "unavailable"
        if self.hass.services.has_service("forecast_solar", "get_forecast"):
            sources = self._sources()
            installations = sorted({source.installation for source in sources})[:MAX_INSTALLATIONS]
            failed = False
            for installation in installations:
                inputs = [self.hass.states.get(source.entity_id) for source in sources if source.installation == installation]
                if not inputs or any(
                    state_quality(state, now, MAX_FORECAST_AGE)
                    or numeric_state(state, "energy", ENERGY_UNITS) is None
                    or state.last_updated.astimezone(AMSTERDAM).date() != now.astimezone(AMSTERDAM).date()
                    for state in inputs
                ):
                    failed = True
                    continue
                try:
                    async with asyncio.timeout(5):
                        response = await self.hass.services.async_call(
                            "forecast_solar", "get_forecast", {"config_entry": installation, "resolution": "raw"},
                            blocking=True, return_response=True,
                        )
                    points = parse_forecast_response(response, now)
                    if points:
                        self._series.append({"installation": installation, "points": points})
                    else:
                        failed = True
                except (HomeAssistantError, TimeoutError, ValueError, TypeError, OverflowError):
                    failed = True
            if self._series:
                self._series_status = "partial" if failed else "available"
        return self._snapshot()
