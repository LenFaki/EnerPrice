"""EnerPrice integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import (
    CONF_DAILY_ADVISOR_ENABLED,
    CONF_GAS_PRICE_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_SMART_SETUP_ENABLED,
    CONF_SOLAR_POWER_ENTITY,
    DAILY_INPUT_KEYS,
    DOMAIN,
    MARKET_PROVIDER_DEFAULTS,
    RUNTIME_DEFAULTS,
    SUPPLIER_UPDATES_AUTOMATIC,
)
from .supplier_registry import supplier_update_mode

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)



async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up integration-level EnerPrice services."""
    from .services import async_register_services

    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EnerPrice from a config entry."""
    from .coordinator import NLDayAheadPricesCoordinator
    from .supplier_registry import get_supplier_profiles

    _LOGGER.info("Setting up EnerPrice config entry %s", entry.entry_id)
    await hass.async_add_executor_job(get_supplier_profiles)
    from .remote_registry import async_get_manager, async_subscribe

    registry_manager = await async_get_manager(hass)
    coordinator = NLDayAheadPricesCoordinator(hass, entry)
    coordinator.registry_manager = registry_manager
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    coordinator.daily_forecast = None
    coordinator.daily_delivery = None
    if {**entry.data, **entry.options}.get(CONF_DAILY_ADVISOR_ENABLED, False):
        from .daily_advisor import DailyForecastCoordinator

        coordinator.daily_forecast = DailyForecastCoordinator(hass, entry)
        from .daily_delivery import DailyAdvisorDelivery

        coordinator.daily_delivery = DailyAdvisorDelivery(coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, _platforms())
    await coordinator.async_start()
    if coordinator.daily_forecast is not None:
        await coordinator.daily_forecast.async_start()
        entry.async_create_background_task(
            hass, coordinator.daily_forecast.async_refresh(), "EnerPrice local solar forecast"
        )
        coordinator.daily_delivery.start_task = entry.async_create_background_task(
            hass, coordinator.daily_delivery.async_start(), "EnerPrice Daily Advisor history"
        )
    entry.async_on_unload(async_subscribe(
        hass, registry_manager, coordinator.registry_updated,
        enabled=supplier_update_mode({**entry.data, **entry.options}) == SUPPLIER_UPDATES_AUTOMATIC,
    ))
    hass.async_create_task(coordinator.async_refresh())
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _LOGGER.info("EnerPrice setup finished for config entry %s", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading EnerPrice config entry %s", entry.entry_id)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, _platforms())
    if unload_ok:
        delivery = getattr(hass.data[DOMAIN][entry.entry_id], "daily_delivery", None)
        if delivery is not None:
            await delivery.async_stop()
        daily = getattr(hass.data[DOMAIN][entry.entry_id], "daily_forecast", None)
        if daily is not None:
            await daily.async_stop()
        await hass.data[DOMAIN][entry.entry_id].async_stop()
        hass.data[DOMAIN].pop(entry.entry_id)
        # Integration-level services remain registered independently of config entries.
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply local settings without fetching; reload only for provider changes."""
    from .remote_registry import async_update_subscription

    coordinator = hass.data[DOMAIN][entry.entry_id]
    previous = coordinator.configured_options
    current = {**entry.data, **entry.options}
    if not _requires_reload(previous, current):
        coordinator.configured_options = current
        for key, default in RUNTIME_DEFAULTS.items():
            if previous.get(key, default) != current.get(key, default):
                coordinator.runtime_options[key] = current.get(key, default)
        async_update_subscription(
            hass, coordinator.registry_manager, coordinator.registry_updated,
            supplier_update_mode(current) == SUPPLIER_UPDATES_AUTOMATIC,
        )
        return
    await hass.config_entries.async_reload(entry.entry_id)


def _platforms() -> list:
    """Return Home Assistant platforms without importing HA during pure module tests."""
    from homeassistant.const import Platform

    return [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SWITCH]


def _requires_reload(previous: dict, current: dict) -> bool:
    """Return whether an option change requires rebuilding platforms/listeners."""
    if any(
        previous.get(key, default) != current.get(key, default)
        for key, default in MARKET_PROVIDER_DEFAULTS.items()
    ):
        return True
    smart_listener_keys = (
        CONF_DAILY_ADVISOR_ENABLED,
        *DAILY_INPUT_KEYS,
        CONF_SMART_SETUP_ENABLED,
        CONF_GRID_POWER_ENTITY,
        CONF_SOLAR_POWER_ENTITY,
        CONF_GAS_PRICE_ENTITY,
    )
    return any(previous.get(key) != current.get(key) for key in smart_listener_keys)
