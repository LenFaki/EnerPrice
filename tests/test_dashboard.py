"""Tests for the adaptive EnerPrice dashboard generator."""

from custom_components.nl_day_ahead_prices.dashboard import generate_dashboard_yaml


def test_dashboard_uses_resolved_entities_and_smart_setup() -> None:
    yaml = generate_dashboard_yaml(
        dashboard_type="energy_advisor",
        language="nl",
        entity_ids={
            "current_all_in_price": "sensor.my_all_in",
            "price_advisor": "sensor.my_advisor",
            "price_score": "sensor.my_score",
        },
        smart_setup={
            "enabled": True,
            "grid_power_entity": "sensor.p1_power",
            "solar_power_entity": "sensor.solar_power",
            "gas_price_entity": "sensor.gas_price",
            "flexible_load_name": "Nymo boiler",
            "flexible_load_power_w": 1500,
        },
    )

    assert "entity: sensor.my_all_in" in yaml
    assert "entity: sensor.my_advisor" in yaml
    assert "entity: sensor.p1_power" in yaml
    assert "entity: sensor.solar_power" in yaml
    assert "entity: sensor.gas_price" in yaml
    assert "Nymo boiler · 1500 W" in yaml
    assert "heading: Smart Energy" in yaml
    assert "title: Energieadvies" in yaml


def test_dashboard_omits_smart_section_when_setup_disabled() -> None:
    yaml = generate_dashboard_yaml(
        dashboard_type="compact",
        language="en",
        smart_setup={"enabled": False},
    )

    assert "heading: Smart Energy" not in yaml
    assert "title: Energy advice" in yaml
    assert "heading: Outlook" not in yaml
    assert "custom:apexcharts-card" not in yaml


def test_dashboard_only_renders_available_optional_smart_entities() -> None:
    yaml = generate_dashboard_yaml(
        language="en",
        smart_setup={
            "enabled": True,
            "grid_power_entity": "sensor.grid",
            "solar_power_entity": None,
            "gas_price_entity": None,
        },
    )

    assert "entity: sensor.grid" in yaml
    assert "Solar production" not in yaml
    assert "Gas price" not in yaml


def test_dashboard_can_hide_smart_energy_context() -> None:
    yaml = generate_dashboard_yaml(
        include_smart_energy=False,
        smart_setup={"enabled": True, "grid_power_entity": "sensor.grid"},
    )

    assert "entity: sensor.grid" not in yaml
    assert "heading: Smart Energy" not in yaml


def test_dashboard_localizes_dynamic_advisor_and_chart_labels() -> None:
    nl = generate_dashboard_yaml(language="nl")
    en = generate_dashboard_yaml(language="en")

    assert "**Volgende gunstiger prijs:**" in nl
    assert "label: Nu" in nl
    assert "**Next better price:**" in en
    assert "label: Now" in en
    assert "Volgende gunstiger prijs" not in en


def test_dashboard_prominently_renders_smart_energy_advisor() -> None:
    yaml = generate_dashboard_yaml(
        language="nl",
        entity_ids={"smart_energy_advisor": "sensor.my_smart_energy_advisor"},
        smart_setup={"enabled": True, "grid_power_entity": "sensor.grid_power"},
    )

    assert "entity: sensor.my_smart_energy_advisor" in yaml
    assert "title: Smart Energy advies" in yaml
    assert "effective_electric_heat_cost_per_kwh" in yaml
    assert "gas_heat_cost_per_kwh" in yaml
    assert "next_better_time" in yaml

def test_dashboard_does_not_guess_missing_optional_registry_entities() -> None:
    yaml = generate_dashboard_yaml(
        dashboard_type="energy_advisor",
        language="nl",
        entity_ids={
            "current_all_in_price": "sensor.real_all_in",
            "current_market_price": "sensor.real_market",
            "price_advisor": "sensor.real_advisor",
            "price_score": "sensor.real_score",
            "tomorrow_prices_available": "binary_sensor.real_tomorrow",
            "selected_supplier": "sensor.real_supplier",
            "current_provider": "sensor.real_provider",
        },
        smart_setup={"enabled": False},
    )

    assert "nl_day_ahead_prices_best_price_period" not in yaml
    assert "nl_day_ahead_prices_next_best_price_period_start" not in yaml


def test_dashboard_hides_missing_advisor_copy_and_uses_clear_device_label() -> None:
    yaml = generate_dashboard_yaml(
        language="nl",
        smart_setup={
            "enabled": True,
            "flexible_load_name": "Nymo WaterAccu",
            "flexible_load_power_w": 2800,
        },
    )

    assert "summary or state_attr" in yaml
    assert "Flexibel apparaat:** Nymo WaterAccu · 2800 W" in yaml
    assert "Flexibele verbruiker" not in yaml
