"""EnerPrice v2 response services."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .calculations import all_in_entries_for_supplier, calculate_supplier_export_fee
from .const import (
    CONF_ADVICE_LANGUAGE,
    CONF_ELECTRIC_EFFICIENCY,
    CONF_FLEXIBLE_LOAD_NAME,
    CONF_FLEXIBLE_LOAD_POWER_W,
    CONF_GAS_EFFICIENCY,
    CONF_GAS_KWH_PER_M3,
    CONF_GAS_PRICE_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_SMART_SETUP_ENABLED,
    CONF_SOLAR_POWER_ENTITY,
    CONF_SOLAR_SURPLUS_THRESHOLD_W,
    DEFAULT_ADVICE_LANGUAGE,
    DEFAULT_ELECTRIC_EFFICIENCY,
    DEFAULT_GAS_EFFICIENCY,
    DEFAULT_GAS_KWH_PER_M3,
    DEFAULT_SOLAR_SURPLUS_THRESHOLD_W,
    DOMAIN,
)
from .dashboard import generate_automation_yaml, generate_dashboard_yaml
from .models import PriceEntry, current_price
from .planning import plan_appliance, plan_battery, plan_ev_charging, plan_export, plan_heating
from .sensor import _energy_tax, _selected_supplier_profile, _vat
from .smart_energy import build_smart_energy_advice

SERVICE_SCHEMAS = {
    "find_best_charging_window": vol.Schema(
        {
            vol.Required("target_energy_kwh"): vol.All(vol.Coerce(float), vol.Range(min=0.01)),
            vol.Required("max_power_kw"): vol.All(vol.Coerce(float), vol.Range(min=0.01)),
            vol.Optional("earliest_start"): cv.datetime,
            vol.Required("deadline"): cv.datetime,
            vol.Optional("price_type", default="all_in"): vol.In(["market", "all_in"]),
            vol.Optional("require_consecutive", default=True): cv.boolean,
            vol.Optional("minimum_session_minutes", default=60): vol.All(vol.Coerce(int), vol.Range(min=15)),
            vol.Optional("allow_split_sessions", default=False): cv.boolean,
            vol.Optional("round_to_resolution", default=True): cv.boolean,
        }
    ),
    "find_best_heating_window": vol.Schema(
        {
            vol.Required("duration_minutes"): vol.All(vol.Coerce(int), vol.Range(min=15)),
            vol.Optional("earliest_start"): cv.datetime,
            vol.Required("deadline"): cv.datetime,
            vol.Optional("prefer_before_peak", default=True): cv.boolean,
            vol.Optional("price_type", default="all_in"): vol.In(["market", "all_in"]),
            vol.Optional("minimum_gap_minutes", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
        }
    ),
    "find_battery_strategy": vol.Schema(
        {
            vol.Required("battery_capacity_kwh"): vol.All(vol.Coerce(float), vol.Range(min=0.1)),
            vol.Required("current_soc_percent"): vol.Coerce(float),
            vol.Required("min_soc_percent"): vol.Coerce(float),
            vol.Required("max_soc_percent"): vol.Coerce(float),
            vol.Required("charge_power_kw"): vol.All(vol.Coerce(float), vol.Range(min=0.1)),
            vol.Required("discharge_power_kw"): vol.All(vol.Coerce(float), vol.Range(min=0.1)),
            vol.Optional("roundtrip_efficiency_percent", default=90): vol.All(
                vol.Coerce(float), vol.Range(min=1, max=100)
            ),
            vol.Optional("price_type", default="all_in"): vol.In(["market", "all_in"]),
            vol.Optional("allow_grid_charge", default=True): cv.boolean,
            vol.Optional("allow_export", default=False): cv.boolean,
        }
    ),
    "find_best_export_window": vol.Schema(
        {
            vol.Optional("expected_export_kwh"): vol.Coerce(float),
            vol.Optional("duration_minutes", default=60): vol.All(vol.Coerce(int), vol.Range(min=15)),
            vol.Optional("price_type", default="sell"): vol.In(["market", "all_in", "sell"]),
            vol.Optional("include_supplier_sell_fee", default=True): cv.boolean,
        }
    ),
    "find_best_appliance_window": vol.Schema(
        {
            vol.Required("appliance_name"): cv.string,
            vol.Required("duration_minutes"): vol.All(vol.Coerce(int), vol.Range(min=15)),
            vol.Optional("earliest_start"): cv.datetime,
            vol.Required("deadline"): cv.datetime,
            vol.Optional("energy_kwh"): vol.Coerce(float),
            vol.Optional("require_consecutive", default=True): cv.boolean,
            vol.Optional("avoid_peak_periods", default=True): cv.boolean,
        }
    ),
    "get_smart_energy_advice": vol.Schema(
        {
            vol.Optional("config_entry_id"): cv.string,
            vol.Optional("gas_price_entity"): cv.entity_id,
            vol.Optional("solar_power_entity"): cv.entity_id,
            vol.Optional("grid_power_entity"): cv.entity_id,
            vol.Optional("gas_price_per_m3"): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional("solar_power_w"): vol.Coerce(float),
            vol.Optional("grid_power_w"): vol.Coerce(float),
            vol.Optional("electric_efficiency"): vol.All(vol.Coerce(float), vol.Range(min=0.01)),
            vol.Optional("gas_efficiency"): vol.All(vol.Coerce(float), vol.Range(min=0.01, max=1)),
            vol.Optional("gas_kwh_per_m3"): vol.All(vol.Coerce(float), vol.Range(min=0.01)),
            vol.Optional("solar_surplus_threshold_w"): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional("flexible_load_power_w"): vol.All(vol.Coerce(float), vol.Range(min=0.01)),
            vol.Optional("language"): vol.In(["en", "nl"]),
        }
    ),
    "generate_dashboard_yaml": vol.Schema(
        {
            vol.Optional("config_entry_id"): cv.string,
            vol.Optional("dashboard_type", default="full"): vol.In(["compact", "full", "energy_advisor"]),
            vol.Optional("include_market_price", default=True): cv.boolean,
            vol.Optional("include_all_in_price", default=True): cv.boolean,
            vol.Optional("include_supplier_info", default=True): cv.boolean,
            vol.Optional("include_best_periods", default=True): cv.boolean,
            vol.Optional("include_price_advisor", default=True): cv.boolean,
            vol.Optional("include_ev_planner", default=False): cv.boolean,
            vol.Optional("include_battery_strategy", default=False): cv.boolean,
            vol.Optional("include_smart_energy", default=True): cv.boolean,
            vol.Optional("include_daily_advisor", default=False): cv.boolean,
            vol.Optional("use_apexcharts", default=True): cv.boolean,
            vol.Optional("theme", default="auto"): vol.In(["auto", "light", "dark"]),
            vol.Optional("language", default="auto"): vol.In(["auto", "en", "nl"]),
        }
    ),
    "generate_automation_yaml": vol.Schema(
        {
            vol.Required("automation_type"): vol.In(
                [
                    "boiler_best_period",
                    "ev_charge_before_deadline",
                    "notify_expensive_period",
                    "notify_cheap_period",
                    "battery_charge_discharge",
                    "appliance_best_window",
                ]
            ),
            vol.Required("target_entity"): cv.entity_id,
            vol.Optional("notify_service", default="notify.notify"): cv.service,
            vol.Optional("duration_minutes", default=120): vol.Coerce(int),
            vol.Optional("deadline"): cv.string,
        }
    ),
}


def async_register_v2_services(hass: HomeAssistant) -> None:
    """Register all v2 planner and generator services."""

    async def handler(call: ServiceCall) -> dict[str, Any]:
        data = dict(call.data)
        name = call.service
        if name == "generate_dashboard_yaml":
            config_entry_id = data.pop("config_entry_id", None)
            coordinator = _coordinator(hass, config_entry_id)
            if coordinator is None:
                return {
                    "error": (
                        "EnerPrice config entry could not be selected. "
                        "Set config_entry_id when multiple entries are loaded."
                    )
                }
            entity_ids = _dashboard_entity_ids(hass, coordinator.entry.entry_id)
            language = data.get("language", "auto")
            if language == "auto":
                data["language"] = "nl" if hass.config.language.startswith("nl") else "en"
            return {
                "yaml": generate_dashboard_yaml(
                    **data,
                    entity_ids=entity_ids,
                    smart_setup=_dashboard_smart_setup(coordinator.entry),
                )
            }
        if name == "generate_automation_yaml":
            return {"yaml": generate_automation_yaml(**data)}
        coordinator = _coordinator(hass, data.pop("config_entry_id", None))
        if coordinator is None or coordinator.data is None:
            if coordinator is not None and name == "get_smart_energy_advice" and getattr(coordinator, "daily_forecast", None) is not None:
                from .daily_advisor import daily_advice

                return {"state": "normal", "daily_advisor": daily_advice(coordinator, data.get("language"))}
            return {"error": "Price data is not available"}
        if name == "get_smart_energy_advice":
            prices = _prices(coordinator, "all_in", True)
            now = dt_util.now()
            stored = _smart_setup_defaults(coordinator.entry)
            gas_price = data.pop("gas_price_per_m3", None)
            solar_power = data.pop("solar_power_w", None)
            grid_power = data.pop("grid_power_w", None)
            gas_entity = data.pop("gas_price_entity", stored.get("gas_price_entity"))
            solar_entity = data.pop("solar_power_entity", stored.get("solar_power_entity"))
            grid_entity = data.pop("grid_power_entity", stored.get("grid_power_entity"))
            if gas_price is None:
                gas_price = _state_float(hass, gas_entity)
            if solar_power is None:
                solar_power = _state_float(hass, solar_entity)
            if grid_power is None:
                grid_power = _state_float(hass, grid_entity)
            for key in ("gas_price_entity", "solar_power_entity", "grid_power_entity"):
                stored.pop(key, None)
            for key, value in stored.items():
                data.setdefault(key, value)
            context = None
            if getattr(coordinator, "daily_forecast", None) is not None:
                from .daily_advisor import advisor_context

                context = advisor_context(coordinator)
            result = build_smart_energy_advice(
                electricity_price=current_price(prices, now),
                future_prices=prices,
                now=now,
                gas_price_per_m3=gas_price,
                solar_power_w=solar_power,
                grid_power_w=grid_power,
                daily_context=context,
                **data,
            )
            return _serialize(result)
        prices = _prices(coordinator, data.pop("price_type", "all_in"), data.get("include_supplier_sell_fee", True))
        now = dt_util.now()
        data.setdefault("earliest_start", now)
        if name == "find_best_charging_window":
            result = plan_ev_charging(prices, **data)
        elif name == "find_best_heating_window":
            result = plan_heating(prices, **data)
        elif name == "find_battery_strategy":
            data.pop("earliest_start", None)
            result = plan_battery(prices, **data)
        elif name == "find_best_export_window":
            data.pop("earliest_start", None)
            data.pop("include_supplier_sell_fee", None)
            result = plan_export(prices, **data)
        else:
            result = plan_appliance(prices, **data)
        return _serialize(result)

    for service, schema in SERVICE_SCHEMAS.items():
        if not hass.services.has_service(DOMAIN, service):
            hass.services.async_register(
                DOMAIN,
                service,
                handler,
                schema=schema,
                supports_response=SupportsResponse.ONLY,
            )


def async_unregister_v2_services(hass: HomeAssistant) -> None:
    """Remove all v2 services."""
    for service in SERVICE_SCHEMAS:
        hass.services.async_remove(DOMAIN, service)


def _coordinator(hass: HomeAssistant, config_entry_id: str | None = None):
    entries = hass.data.get(DOMAIN, {})
    if config_entry_id:
        return entries.get(config_entry_id)
    if len(entries) == 1:
        return next(iter(entries.values()))
    return None


def _dashboard_entity_ids(hass: HomeAssistant, config_entry_id: str) -> dict[str, str]:
    """Resolve actual entity IDs for one EnerPrice config entry."""
    registry = er.async_get(hass)
    by_unique_suffix: dict[str, str] = {}
    for entity in er.async_entries_for_config_entry(registry, config_entry_id):
        if entity.platform != DOMAIN or entity.disabled_by is not None:
            continue
        # A stale Entity Registry entry can survive after an entity is no longer
        # provided by the integration. Only expose entities that currently have
        # a state in Home Assistant so generated dashboards cannot contain
        # "Entity not found" cards. Keep temporarily unavailable entities: they
        # still have a State object and are valid dashboard targets.
        if hass.states.get(entity.entity_id) is None:
            continue
        for key in (
            "price_advisor",
            "price_score",
            "current_all_in_price",
            "current_market_price",
            "tomorrow_prices_available",
            "best_price_period",
            "next_best_price_period_start",
            "selected_supplier",
            "current_provider",
            "smart_energy_advisor",
            "solar_forecast_today",
            "solar_forecast_tomorrow",
            "daily_forecast_quality",
            "daily_energy_advice",
            "daily_notification_status",
            "daily_energy_performance",
            "best_consumption_window",
        ):
            if entity.unique_id.endswith(f"_{key}"):
                by_unique_suffix[key] = entity.entity_id
    return by_unique_suffix


def _prices(coordinator, price_type: str, include_sell_fee: bool) -> list[PriceEntry]:
    entry = coordinator.entry
    profile = _selected_supplier_profile(entry)
    if price_type == "market":
        return coordinator.data.result.prices
    if price_type == "sell":
        fee = calculate_supplier_export_fee(profile, _vat(entry)) if include_sell_fee else 0.0
        return [PriceEntry(item.time, item.price * (1 + _vat(entry)) - fee) for item in coordinator.data.result.prices]
    return all_in_entries_for_supplier(coordinator.data.result.prices, _energy_tax(entry), profile, _vat(entry))


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value



def _smart_setup_defaults(entry) -> dict[str, Any]:
    """Return stored Smart Setup values as service argument defaults."""
    options = {**entry.data, **entry.options}
    if not options.get(CONF_SMART_SETUP_ENABLED, False):
        return {}

    defaults = {
        "gas_price_entity": options.get(CONF_GAS_PRICE_ENTITY),
        "solar_power_entity": options.get(CONF_SOLAR_POWER_ENTITY),
        "grid_power_entity": options.get(CONF_GRID_POWER_ENTITY),
        "flexible_load_power_w": options.get(CONF_FLEXIBLE_LOAD_POWER_W),
        "electric_efficiency": options.get(CONF_ELECTRIC_EFFICIENCY, DEFAULT_ELECTRIC_EFFICIENCY),
        "gas_efficiency": options.get(CONF_GAS_EFFICIENCY, DEFAULT_GAS_EFFICIENCY),
        "gas_kwh_per_m3": options.get(CONF_GAS_KWH_PER_M3, DEFAULT_GAS_KWH_PER_M3),
        "solar_surplus_threshold_w": options.get(
            CONF_SOLAR_SURPLUS_THRESHOLD_W, DEFAULT_SOLAR_SURPLUS_THRESHOLD_W
        ),
        "language": options.get(CONF_ADVICE_LANGUAGE, DEFAULT_ADVICE_LANGUAGE),
    }
    return {key: value for key, value in defaults.items() if value is not None}



def _state_float(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """Read a numeric Home Assistant state without coupling to a vendor integration."""
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in {"unknown", "unavailable", "none", ""}:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def _dashboard_smart_setup(entry) -> dict[str, Any]:
    """Return non-sensitive Smart Setup context for the dashboard generator."""
    options = {**entry.data, **entry.options}
    if not options.get(CONF_SMART_SETUP_ENABLED, False):
        return {"enabled": False}
    return {
        "enabled": True,
        "grid_power_entity": options.get(CONF_GRID_POWER_ENTITY),
        "solar_power_entity": options.get(CONF_SOLAR_POWER_ENTITY),
        "gas_price_entity": options.get(CONF_GAS_PRICE_ENTITY),
        "flexible_load_name": options.get(CONF_FLEXIBLE_LOAD_NAME),
        "flexible_load_power_w": options.get(CONF_FLEXIBLE_LOAD_POWER_W),
    }
