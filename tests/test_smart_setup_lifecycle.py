"""Phase 6 lifecycle regressions for v2.5 Smart Setup."""

from custom_components.nl_day_ahead_prices import _requires_reload
from custom_components.nl_day_ahead_prices.const import (
    CONF_ADVICE_LANGUAGE,
    CONF_FLEXIBLE_LOAD_POWER_W,
    CONF_GAS_PRICE_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_SMART_SETUP_ENABLED,
    CONF_SOLAR_POWER_ENTITY,
)


def test_enabling_or_disabling_smart_setup_requires_reload():
    assert _requires_reload({}, {CONF_SMART_SETUP_ENABLED: True})
    assert _requires_reload({CONF_SMART_SETUP_ENABLED: True}, {CONF_SMART_SETUP_ENABLED: False})


def test_changing_live_input_entities_requires_reload():
    base = {CONF_SMART_SETUP_ENABLED: True}
    for key in (CONF_GRID_POWER_ENTITY, CONF_SOLAR_POWER_ENTITY, CONF_GAS_PRICE_ENTITY):
        previous = {**base, key: "sensor.old"}
        current = {**base, key: "sensor.new"}
        assert _requires_reload(previous, current)


def test_non_listener_smart_settings_do_not_force_reload():
    previous = {
        CONF_SMART_SETUP_ENABLED: True,
        CONF_GRID_POWER_ENTITY: "sensor.grid",
        CONF_FLEXIBLE_LOAD_POWER_W: 1500,
        CONF_ADVICE_LANGUAGE: "nl",
    }
    current = {
        **previous,
        CONF_FLEXIBLE_LOAD_POWER_W: 1800,
        CONF_ADVICE_LANGUAGE: "en",
    }
    assert not _requires_reload(previous, current)
