"""EnerPrice v2 response services."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .calculations import calculate_all_in_price, calculate_sell_price
from .const import DOMAIN
from .dashboard import generate_automation_yaml, generate_dashboard_yaml
from .models import PriceEntry
from .planning import plan_appliance, plan_battery, plan_ev_charging, plan_export, plan_heating
from .sensor import _energy_tax, _selected_supplier_profile, _vat

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
    "generate_dashboard_yaml": vol.Schema(
        {
            vol.Optional("dashboard_type", default="full"): vol.In(["compact", "full", "energy_advisor"]),
            vol.Optional("include_market_price", default=True): cv.boolean,
            vol.Optional("include_all_in_price", default=True): cv.boolean,
            vol.Optional("include_supplier_info", default=True): cv.boolean,
            vol.Optional("include_best_periods", default=True): cv.boolean,
            vol.Optional("include_price_advisor", default=True): cv.boolean,
            vol.Optional("include_ev_planner", default=False): cv.boolean,
            vol.Optional("include_battery_strategy", default=False): cv.boolean,
            vol.Optional("theme", default="auto"): vol.In(["auto", "light", "dark"]),
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
            return {"yaml": generate_dashboard_yaml(**data)}
        if name == "generate_automation_yaml":
            return {"yaml": generate_automation_yaml(**data)}
        coordinator = _coordinator(hass)
        if coordinator is None or coordinator.data is None:
            return {"error": "Price data is not available"}
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


def _coordinator(hass: HomeAssistant):
    return next(iter(hass.data.get(DOMAIN, {}).values()), None)


def _prices(coordinator, price_type: str, include_sell_fee: bool) -> list[PriceEntry]:
    entry = coordinator.entry
    profile = _selected_supplier_profile(entry)
    if price_type == "market":
        return coordinator.data.result.prices
    if price_type == "sell":
        if not include_sell_fee:
            return [PriceEntry(item.time, item.price) for item in coordinator.data.result.prices]
        return [
            PriceEntry(item.time, calculate_sell_price(item.price, profile, _vat(entry)))
            for item in coordinator.data.result.prices
        ]
    return [
        PriceEntry(item.time, calculate_all_in_price(item.price, _energy_tax(entry), profile, _vat(entry)))
        for item in coordinator.data.result.prices
    ]


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value
