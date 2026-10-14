"""Config and options flow for EnerPrice."""

from __future__ import annotations

from functools import partial
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TimeSelector,
)

from .const import (
    CONF_ADVICE_LANGUAGE,
    CONF_COUNTRY,
    CONF_CURRENCY,
    CONF_CUSTOM_MONTHLY_FEE_ELECTRICITY,
    CONF_CUSTOM_PURCHASE_FEE_ELECTRICITY,
    CONF_CUSTOM_PURCHASE_FEE_INCLUDES_VAT,
    CONF_CUSTOM_SELL_FEE_ELECTRICITY,
    CONF_CUSTOM_SELL_FEE_INCLUDES_VAT,
    CONF_CUSTOM_SELL_MARKET_PRICE_INCLUDES_VAT,
    CONF_CUSTOM_SUPPLIER_NAME,
    CONF_DAILY_ADVISOR_ENABLED,
    CONF_DAILY_CONSUMPTION_ENTITIES,
    CONF_DAILY_DURATION_MINUTES,
    CONF_DAILY_EXPORT_ENERGY_ENTITY,
    CONF_DAILY_FORECAST_ENTITIES,
    CONF_DAILY_GRID_POWER_ENTITY,
    CONF_DAILY_IMPORT_ENERGY_ENTITY,
    CONF_DAILY_NOTIFY_CONSENT,
    CONF_DAILY_NOTIFY_ENABLED,
    CONF_DAILY_NOTIFY_TARGET,
    CONF_DAILY_NOTIFY_TIME,
    CONF_DAILY_RETENTION_DAYS,
    CONF_DAILY_SOLAR_ENERGY_ENTITY,
    CONF_DAILY_SOLAR_POWER_ENTITY,
    CONF_ELECTRIC_EFFICIENCY,
    CONF_ENABLE_ENTSOE,
    CONF_ENERGY_TAX,
    CONF_ENERGY_TAX_INCL_VAT,
    CONF_ENTSOE_API_TOKEN,
    CONF_FLEXIBLE_LOAD_NAME,
    CONF_FLEXIBLE_LOAD_POWER_W,
    CONF_GAS_EFFICIENCY,
    CONF_GAS_KWH_PER_M3,
    CONF_GAS_PRICE_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_PRICE_RESOLUTION,
    CONF_PRIMARY_PROVIDER,
    CONF_SELECTED_SUPPLIER,
    CONF_SMART_SETUP_ENABLED,
    CONF_SOLAR_POWER_ENTITY,
    CONF_SOLAR_SURPLUS_THRESHOLD_W,
    CONF_SUPPLIER_TARIFF_UPDATES,
    CONF_VAT,
    DAILY_CONSENT_TARGET,
    DEFAULT_ADVICE_LANGUAGE,
    DEFAULT_COUNTRY,
    DEFAULT_CURRENCY,
    DEFAULT_CUSTOM_MONTHLY_FEE_ELECTRICITY,
    DEFAULT_CUSTOM_PURCHASE_FEE_ELECTRICITY,
    DEFAULT_CUSTOM_PURCHASE_FEE_INCLUDES_VAT,
    DEFAULT_CUSTOM_SELL_FEE_ELECTRICITY,
    DEFAULT_CUSTOM_SELL_FEE_INCLUDES_VAT,
    DEFAULT_CUSTOM_SELL_MARKET_PRICE_INCLUDES_VAT,
    DEFAULT_CUSTOM_SUPPLIER_NAME,
    DEFAULT_ELECTRIC_EFFICIENCY,
    DEFAULT_ENERGY_TAX,
    DEFAULT_FLEXIBLE_LOAD_NAME,
    DEFAULT_GAS_EFFICIENCY,
    DEFAULT_GAS_KWH_PER_M3,
    DEFAULT_PRICE_RESOLUTION,
    DEFAULT_PRIMARY_PROVIDER,
    DEFAULT_SELECTED_SUPPLIER,
    DEFAULT_SMART_SETUP_ENABLED,
    DEFAULT_SOLAR_SURPLUS_THRESHOLD_W,
    DEFAULT_VAT,
    DOMAIN,
    NAME,
    PROVIDER_ENERGY_CHARTS,
    PROVIDER_NORD_POOL,
    SUPPLIER_UPDATES_AUTOMATIC,
    SUPPLIER_UPDATES_BUNDLED,
)
from .price_resolution import PRICE_RESOLUTION_AUTO, PRICE_RESOLUTION_HOURLY, PRICE_RESOLUTION_QUARTER_HOUR
from .remote_registry import STORAGE_KEY
from .supplier_registry import get_supplier_profiles, supplier_update_mode

CUSTOM_SUPPLIER_KEY = "custom"


class NLDayAheadPricesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Create a config entry."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        schema = vol.Schema(
            {
                vol.Optional(CONF_NAME, default=NAME): str,
                vol.Optional(CONF_COUNTRY, default=DEFAULT_COUNTRY): str,
                vol.Optional(CONF_CURRENCY, default=DEFAULT_CURRENCY): str,
                vol.Optional(CONF_PRIMARY_PROVIDER, default=DEFAULT_PRIMARY_PROVIDER): vol.In(
                    [PROVIDER_NORD_POOL, PROVIDER_ENERGY_CHARTS]
                ),
                vol.Optional(CONF_ENABLE_ENTSOE, default=False): bool,
                vol.Optional(CONF_ENTSOE_API_TOKEN, default=""): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> NLDayAheadPricesOptionsFlow:
        """Return options flow handler."""
        return NLDayAheadPricesOptionsFlow()


class NLDayAheadPricesOptionsFlow(config_entries.OptionsFlow):
    """Options flow for taxes, VAT, providers and fallback."""

    def __init__(self) -> None:
        """Initialize options flow state."""
        self._pending_options: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage options."""
        data = self._current_options()
        if user_input is not None:
            errors = _validate_base_options(user_input)
            if errors:
                return self.async_show_form(
                    step_id="init",
                    data_schema=await self._async_base_options_schema({**data, **user_input}),
                    errors=errors,
                )
            self._pending_options = {**data, **user_input}
            if self._pending_options.get(CONF_SELECTED_SUPPLIER) == CUSTOM_SUPPLIER_KEY:
                return await self.async_step_custom_supplier()
            return await self.async_step_supplier_summary()

        return self.async_show_form(step_id="init", data_schema=await self._async_base_options_schema(data))

    async def async_step_supplier_summary(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show selected supplier profile details before saving."""
        if user_input is not None:
            return await self.async_step_smart_setup()

        profiles = await self._async_supplier_profiles(self._pending_options)
        selected = str(self._pending_options.get(CONF_SELECTED_SUPPLIER, DEFAULT_SELECTED_SUPPLIER))
        profile = profiles.get(selected)
        if profile is None:
            return self.async_show_form(
                step_id="supplier_summary",
                data_schema=vol.Schema({}),
                errors={"base": "supplier_profile_unavailable"},
            )

        return self.async_show_form(
            step_id="supplier_summary",
            data_schema=vol.Schema({}),
            description_placeholders={
                "supplier": profile.name,
                "purchase_fee": f"{profile.purchase_fee_electricity:.4f} EUR/kWh",
                "purchase_fee_vat": "incl. VAT" if profile.purchase_fee_includes_vat else "excl. VAT",
                "monthly_fee": f"{profile.monthly_fee_electricity:.2f} EUR/month",
                "last_verified": profile.last_verified or "unknown",
                "source_url": profile.source_url or "unknown",
            },
        )

    async def async_step_custom_supplier(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure custom supplier fees."""
        data = self._pending_options or self._current_options()
        if user_input is not None:
            errors = _validate_custom_supplier_options(user_input)
            if errors:
                return self.async_show_form(
                    step_id="custom_supplier",
                    data_schema=self._custom_supplier_schema({**data, **user_input}),
                    errors=errors,
                )
            self._pending_options = {**data, **user_input}
            return await self.async_step_smart_setup()

        return self.async_show_form(step_id="custom_supplier", data_schema=self._custom_supplier_schema(data))

    async def async_step_smart_setup(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure optional Smart Energy defaults."""
        data = self._pending_options or self._current_options()
        if user_input is not None:
            enabled = bool(user_input.get(CONF_SMART_SETUP_ENABLED, DEFAULT_SMART_SETUP_ENABLED))
            if not enabled:
                # Disabling Smart Setup must not erase the user's device selections,
                # efficiencies or language. This makes the toggle reversible.
                return await self._async_finish_smart_setup(_disabled_smart_setup_options(data, user_input))
            errors = _validate_smart_setup(user_input)
            if errors:
                return self.async_show_form(
                    step_id="smart_setup",
                    data_schema=self._smart_setup_schema({**data, **user_input}),
                    errors=errors,
                )
            return await self._async_finish_smart_setup({**data, **user_input})

        return self.async_show_form(step_id="smart_setup", data_schema=self._smart_setup_schema(data))

    async def _async_finish_smart_setup(self, data):
        self._pending_options = data
        if data.get(CONF_DAILY_ADVISOR_ENABLED, False):
            return await self.async_step_daily_advisor()
        return self.async_create_entry(title="", data=data)

    async def async_step_daily_advisor(self, user_input=None):
        """Configure the opt-in forecast foundation independently of Smart Setup."""
        from .daily_advisor import validate_daily_inputs
        from .daily_delivery import notification_preference, notification_targets, validate_delivery_options

        data = self._pending_options or self._current_options()
        errors = {}
        if user_input is not None:
            candidate = {**data, **user_input}
            if CONF_DAILY_NOTIFY_CONSENT in user_input:
                candidate[DAILY_CONSENT_TARGET] = candidate.get(CONF_DAILY_NOTIFY_TARGET) if user_input[CONF_DAILY_NOTIFY_CONSENT] is True else None
            elif candidate.get(CONF_DAILY_NOTIFY_TARGET) != data.get(CONF_DAILY_NOTIFY_TARGET):
                candidate[DAILY_CONSENT_TARGET] = None
            # Empty selections explicitly clear inputs; omitted fields preserve them.
            errors = validate_daily_inputs(self.hass, candidate)
            errors.update(validate_delivery_options(self.hass, candidate))
            if not errors:
                return self.async_create_entry(title="", data=candidate)
            data = candidate
        targets = notification_targets(self.hass)
        selected_target = data.get(CONF_DAILY_NOTIFY_TARGET, "")
        schema = {
            vol.Optional(CONF_DAILY_NOTIFY_ENABLED, default=data.get(CONF_DAILY_NOTIFY_ENABLED, notification_preference(self._current_options()))): bool,
            vol.Optional(CONF_DAILY_NOTIFY_CONSENT, default=data.get(CONF_DAILY_NOTIFY_CONSENT, False)): bool,
            vol.Optional(CONF_DAILY_NOTIFY_TARGET, default=selected_target if selected_target in targets else ""):
                SelectSelector(SelectSelectorConfig(options=["", *targets])),
            vol.Optional(CONF_DAILY_NOTIFY_TIME, default=data.get(CONF_DAILY_NOTIFY_TIME, "08:00:00")): TimeSelector(),
            vol.Optional(CONF_DAILY_RETENTION_DAYS, default=data.get(CONF_DAILY_RETENTION_DAYS, 30)):
                NumberSelector(NumberSelectorConfig(min=1, max=90, step=1, mode=NumberSelectorMode.BOX)),
            vol.Optional(CONF_DAILY_DURATION_MINUTES, default=data.get(CONF_DAILY_DURATION_MINUTES, 60)):
                NumberSelector(NumberSelectorConfig(min=15, max=1440, step=15, unit_of_measurement="min", mode=NumberSelectorMode.BOX)),
            vol.Optional(CONF_DAILY_FORECAST_ENTITIES, default=data.get(CONF_DAILY_FORECAST_ENTITIES, [])):
                EntitySelector(EntitySelectorConfig(domain="sensor", integration="forecast_solar", device_class="energy", multiple=True)),
            vol.Optional(CONF_DAILY_CONSUMPTION_ENTITIES, default=data.get(CONF_DAILY_CONSUMPTION_ENTITIES, [])):
                EntitySelector(EntitySelectorConfig(domain="sensor", device_class="energy", multiple=True)),
        }
        # Lists of at most one entity allow explicit clearing with an empty list.
        for key, device_class in ((CONF_DAILY_GRID_POWER_ENTITY, "power"), (CONF_DAILY_SOLAR_ENERGY_ENTITY, "energy"), (CONF_DAILY_SOLAR_POWER_ENTITY, "power"),
                                  (CONF_DAILY_IMPORT_ENERGY_ENTITY, "energy"), (CONF_DAILY_EXPORT_ENERGY_ENTITY, "energy")):
            schema[vol.Optional(key, default=data.get(key, []))] = EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class=device_class, multiple=True)
            )
        return self.async_show_form(step_id="daily_advisor", data_schema=vol.Schema(schema), errors=errors)

    def _smart_setup_schema(self, data: dict[str, Any]) -> vol.Schema:
        """Return optional Smart Energy setup schema."""
        power_entity = EntitySelector(EntitySelectorConfig(domain="sensor", device_class="power"))
        schema: dict[Any, Any] = {
            vol.Optional(
                CONF_SMART_SETUP_ENABLED,
                default=data.get(CONF_SMART_SETUP_ENABLED, DEFAULT_SMART_SETUP_ENABLED),
            ): bool,
            vol.Optional(
                CONF_FLEXIBLE_LOAD_NAME,
                default=data.get(CONF_FLEXIBLE_LOAD_NAME, DEFAULT_FLEXIBLE_LOAD_NAME),
            ): str,
            vol.Optional(
                CONF_ELECTRIC_EFFICIENCY,
                default=data.get(CONF_ELECTRIC_EFFICIENCY, DEFAULT_ELECTRIC_EFFICIENCY),
            ): NumberSelector(
                NumberSelectorConfig(min=0.01, max=5, step=0.01, mode=NumberSelectorMode.BOX)
            ),
            vol.Optional(
                CONF_GAS_EFFICIENCY,
                default=data.get(CONF_GAS_EFFICIENCY, DEFAULT_GAS_EFFICIENCY),
            ): NumberSelector(
                NumberSelectorConfig(min=0.01, max=1, step=0.01, mode=NumberSelectorMode.BOX)
            ),
            vol.Optional(
                CONF_GAS_KWH_PER_M3,
                default=data.get(CONF_GAS_KWH_PER_M3, DEFAULT_GAS_KWH_PER_M3),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0.01,
                    max=20,
                    step=0.001,
                    unit_of_measurement="kWh/m³",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_SOLAR_SURPLUS_THRESHOLD_W,
                default=data.get(CONF_SOLAR_SURPLUS_THRESHOLD_W, DEFAULT_SOLAR_SURPLUS_THRESHOLD_W),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=50000,
                    step=1,
                    unit_of_measurement="W",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_ADVICE_LANGUAGE,
                default=data.get(CONF_ADVICE_LANGUAGE, DEFAULT_ADVICE_LANGUAGE),
            ): SelectSelector(SelectSelectorConfig(options=["en", "nl"])),
        }

        optional_selectors = (
            (CONF_GRID_POWER_ENTITY, power_entity),
            (CONF_SOLAR_POWER_ENTITY, power_entity),
            (
                CONF_GAS_PRICE_ENTITY,
                EntitySelector(EntitySelectorConfig(domain="sensor")),
            ),
            (
                CONF_FLEXIBLE_LOAD_POWER_W,
                NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=50000,
                        step=1,
                        unit_of_measurement="W",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            ),
        )
        for key, selector in optional_selectors:
            marker = vol.Optional(key, default=data[key]) if key in data else vol.Optional(key)
            schema[marker] = selector

        return vol.Schema(schema)

    def _current_options(self) -> dict[str, Any]:
        """Return merged config entry data and options."""
        return {**self.config_entry.data, **self.config_entry.options}

    async def _async_base_options_schema(self, data: dict[str, Any]) -> vol.Schema:
        profiles = await self._async_supplier_profiles(data)
        supplier_choices = {key: profile.name for key, profile in profiles.items()} or {
            CUSTOM_SUPPLIER_KEY: DEFAULT_CUSTOM_SUPPLIER_NAME
        }
        selected_supplier = data.get(CONF_SELECTED_SUPPLIER, DEFAULT_SELECTED_SUPPLIER)
        if selected_supplier not in supplier_choices:
            selected_supplier = DEFAULT_SELECTED_SUPPLIER if DEFAULT_SELECTED_SUPPLIER in supplier_choices else CUSTOM_SUPPLIER_KEY
        schema = vol.Schema(
            {
                vol.Optional(CONF_COUNTRY, default=data.get(CONF_COUNTRY, DEFAULT_COUNTRY)): str,
                vol.Optional(CONF_DAILY_ADVISOR_ENABLED, default=data.get(CONF_DAILY_ADVISOR_ENABLED, False)): bool,
                vol.Optional(CONF_CURRENCY, default=data.get(CONF_CURRENCY, DEFAULT_CURRENCY)): str,
                vol.Optional(CONF_PRIMARY_PROVIDER, default=data.get(CONF_PRIMARY_PROVIDER, DEFAULT_PRIMARY_PROVIDER)): vol.In(
                    [PROVIDER_NORD_POOL, PROVIDER_ENERGY_CHARTS]
                ),
                vol.Optional(CONF_ENABLE_ENTSOE, default=data.get(CONF_ENABLE_ENTSOE, False)): bool,
                vol.Optional(CONF_ENTSOE_API_TOKEN, default=data.get(CONF_ENTSOE_API_TOKEN, "")): str,
                vol.Optional(
                    CONF_SUPPLIER_TARIFF_UPDATES, default=supplier_update_mode(data),
                ): SelectSelector(SelectSelectorConfig(
                    options=[SUPPLIER_UPDATES_AUTOMATIC, SUPPLIER_UPDATES_BUNDLED],
                    translation_key=CONF_SUPPLIER_TARIFF_UPDATES,
                )),
                vol.Optional(
                    CONF_SELECTED_SUPPLIER,
                    default=selected_supplier,
                ): vol.In(supplier_choices),
                vol.Optional(
                    CONF_PRICE_RESOLUTION,
                    default=data.get(CONF_PRICE_RESOLUTION, DEFAULT_PRICE_RESOLUTION),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            PRICE_RESOLUTION_AUTO,
                            PRICE_RESOLUTION_HOURLY,
                            PRICE_RESOLUTION_QUARTER_HOUR,
                        ],
                        translation_key=CONF_PRICE_RESOLUTION,
                    )
                ),
                vol.Optional(
                    CONF_ENERGY_TAX,
                    default=data.get(CONF_ENERGY_TAX, data.get(CONF_ENERGY_TAX_INCL_VAT, DEFAULT_ENERGY_TAX)),
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Optional(CONF_VAT, default=data.get(CONF_VAT, DEFAULT_VAT)): vol.All(
                    vol.Coerce(float), vol.Range(min=0, max=1)
                ),
            }
        )
        return schema

    async def _async_supplier_profiles(self, data):
        """Preview the pending mode without activating it or starting HTTP."""
        manager = self.hass.data.get(STORAGE_KEY)
        view = manager.cached_registry if manager is not None else None
        return await self.hass.async_add_executor_job(
            partial(get_supplier_profiles, mode=supplier_update_mode(data), remote_view=view)
        )

    def _custom_supplier_schema(self, data: dict[str, Any]) -> vol.Schema:
        """Return schema for custom supplier details."""
        return vol.Schema(
            {
                vol.Optional(
                    CONF_CUSTOM_SUPPLIER_NAME,
                    default=data.get(CONF_CUSTOM_SUPPLIER_NAME, DEFAULT_CUSTOM_SUPPLIER_NAME),
                ): str,
                vol.Optional(
                    CONF_CUSTOM_MONTHLY_FEE_ELECTRICITY,
                    default=data.get(CONF_CUSTOM_MONTHLY_FEE_ELECTRICITY, DEFAULT_CUSTOM_MONTHLY_FEE_ELECTRICITY),
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Optional(
                    CONF_CUSTOM_PURCHASE_FEE_ELECTRICITY,
                    default=data.get(CONF_CUSTOM_PURCHASE_FEE_ELECTRICITY, DEFAULT_CUSTOM_PURCHASE_FEE_ELECTRICITY),
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Optional(
                    CONF_CUSTOM_PURCHASE_FEE_INCLUDES_VAT,
                    default=data.get(
                        CONF_CUSTOM_PURCHASE_FEE_INCLUDES_VAT, DEFAULT_CUSTOM_PURCHASE_FEE_INCLUDES_VAT
                    ),
                ): bool,
                vol.Optional(
                    CONF_CUSTOM_SELL_FEE_ELECTRICITY,
                    default=data.get(CONF_CUSTOM_SELL_FEE_ELECTRICITY, DEFAULT_CUSTOM_SELL_FEE_ELECTRICITY),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_CUSTOM_SELL_FEE_INCLUDES_VAT,
                    default=data.get(CONF_CUSTOM_SELL_FEE_INCLUDES_VAT, DEFAULT_CUSTOM_SELL_FEE_INCLUDES_VAT),
                ): bool,
                vol.Optional(
                    CONF_CUSTOM_SELL_MARKET_PRICE_INCLUDES_VAT,
                    default=data.get(
                        CONF_CUSTOM_SELL_MARKET_PRICE_INCLUDES_VAT, DEFAULT_CUSTOM_SELL_MARKET_PRICE_INCLUDES_VAT
                    ),
                ): bool,
            }
        )


def _validate_base_options(user_input: dict[str, Any]) -> dict[str, str]:
    """Validate main option ranges."""
    errors: dict[str, str] = {}
    vat = _float_option(user_input, CONF_VAT, DEFAULT_VAT)
    energy_tax = _float_option(user_input, CONF_ENERGY_TAX, DEFAULT_ENERGY_TAX)
    if vat is None or vat < 0 or vat > 1:
        errors[CONF_VAT] = "invalid_vat"
    if energy_tax is None or energy_tax < 0:
        errors[CONF_ENERGY_TAX] = "negative_value"
    return errors


def _validate_custom_supplier_options(user_input: dict[str, Any]) -> dict[str, str]:
    """Validate custom supplier option ranges."""
    errors: dict[str, str] = {}
    monthly_fee = _float_option(user_input, CONF_CUSTOM_MONTHLY_FEE_ELECTRICITY, 0)
    purchase_fee = _float_option(user_input, CONF_CUSTOM_PURCHASE_FEE_ELECTRICITY, 0)
    if monthly_fee is None or monthly_fee < 0:
        errors[CONF_CUSTOM_MONTHLY_FEE_ELECTRICITY] = "negative_value"
    if purchase_fee is None or purchase_fee < 0:
        errors[CONF_CUSTOM_PURCHASE_FEE_ELECTRICITY] = "negative_value"
    return errors


def _float_option(user_input: dict[str, Any], key: str, default: float) -> float | None:
    """Read an option as float without raising from the options flow."""
    try:
        return float(user_input.get(key, default))
    except (TypeError, ValueError):
        return None


def _validate_smart_setup(user_input: dict[str, Any]) -> dict[str, str]:
    """Validate Smart Setup values without treating missing entities as zero."""
    errors: dict[str, str] = {}
    if not user_input.get(CONF_SMART_SETUP_ENABLED, False):
        return errors
    load = _float_option(user_input, CONF_FLEXIBLE_LOAD_POWER_W, 0)
    if load is None or load <= 0:
        errors[CONF_FLEXIBLE_LOAD_POWER_W] = "positive_value_required"
    for key in (CONF_ELECTRIC_EFFICIENCY, CONF_GAS_EFFICIENCY, CONF_GAS_KWH_PER_M3):
        value = _float_option(user_input, key, 0)
        if value is None or value <= 0:
            errors[key] = "positive_value_required"
    threshold = _float_option(user_input, CONF_SOLAR_SURPLUS_THRESHOLD_W, DEFAULT_SOLAR_SURPLUS_THRESHOLD_W)
    if threshold is None or threshold < 0:
        errors[CONF_SOLAR_SURPLUS_THRESHOLD_W] = "negative_value"
    return errors


def _disabled_smart_setup_options(data: dict[str, Any], user_input: dict[str, Any]) -> dict[str, Any]:
    """Disable Smart Setup while preserving the user's stored configuration."""
    return {**data, **user_input, CONF_SMART_SETUP_ENABLED: False}
