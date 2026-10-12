from datetime import datetime, timedelta, timezone

import pytest

from custom_components.nl_day_ahead_prices.models import PriceEntry
from custom_components.nl_day_ahead_prices.smart_energy import build_smart_energy_advice

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def test_prefers_solar_surplus():
    result = build_smart_energy_advice(
        electricity_price=0.25,
        gas_price_per_m3=1.30,
        solar_power_w=2400,
        grid_power_w=-900,
        now=NOW,
    )
    assert result["state"] == "solar_surplus"
    assert result["solar_surplus"] is True


def test_compares_useful_heat_costs():
    result = build_smart_energy_advice(
        electricity_price=0.14,
        gas_price_per_m3=1.40,
        electric_efficiency=1.0,
        gas_efficiency=0.90,
        now=NOW,
    )
    assert result["state"] == "cheap_grid"
    assert result["electric_heat_cost_per_kwh"] == 0.14
    assert result["gas_heat_cost_per_kwh"] > 0.15


def test_prefers_gas_when_grid_heat_is_more_expensive():
    result = build_smart_energy_advice(
        electricity_price=0.40,
        gas_price_per_m3=1.20,
        now=NOW,
    )
    assert result["state"] == "gas"
    assert result["cheapest_now"] == "gas"


def test_waits_for_materially_cheaper_future_electricity():
    prices = [
        PriceEntry(NOW + timedelta(minutes=15), 0.30),
        PriceEntry(NOW + timedelta(minutes=30), 0.12),
    ]
    result = build_smart_energy_advice(
        electricity_price=0.30,
        future_prices=prices,
        gas_price_per_m3=1.50,
        now=NOW,
    )
    assert result["state"] == "wait"
    assert result["minutes_until_better"] == 30
    assert result["next_better_price"] == 0.12


def test_negative_price_future_threshold_is_absolute():
    prices = [PriceEntry(NOW + timedelta(minutes=15), -0.02)]
    result = build_smart_energy_advice(
        electricity_price=-0.01,
        future_prices=prices,
        gas_price_per_m3=1.20,
        now=NOW,
    )
    assert result["next_better_price"] == -0.02


def test_missing_gas_price_remains_generic():
    result = build_smart_energy_advice(electricity_price=0.20, now=NOW, language="nl")
    assert result["state"] in {"cheap_grid", "wait"}
    assert "gasprijssensor" in result["recommendation"]


def test_solar_production_is_not_surplus_when_export_is_below_threshold():
    result = build_smart_energy_advice(
        electricity_price=0.25,
        gas_price_per_m3=1.30,
        solar_power_w=2500,
        grid_power_w=-300,
        now=NOW,
    )
    assert result["solar_production"] is True
    assert result["measured_solar_surplus_w"] == 300
    assert result["solar_surplus"] is False


def test_grid_export_can_establish_surplus_without_solar_sensor():
    result = build_smart_energy_advice(
        electricity_price=0.25,
        gas_price_per_m3=1.30,
        grid_power_w=-700,
        now=NOW,
    )
    assert result["solar_surplus"] is True
    assert result["state"] == "solar_surplus"


def test_equal_heat_cost_prefers_electricity():
    gas_price = 0.20 * 9.769 * 0.90
    result = build_smart_energy_advice(
        electricity_price=0.20,
        gas_price_per_m3=gas_price,
        now=NOW,
    )
    assert result["cheapest_now"] == "electricity"
    assert result["state"] == "cheap_grid"


def test_future_price_at_exact_five_percent_threshold_is_detected():
    prices = [PriceEntry(NOW + timedelta(minutes=15), 0.19)]
    result = build_smart_energy_advice(
        electricity_price=0.20,
        future_prices=prices,
        now=NOW,
    )
    assert result["next_better_price"] == 0.19
    assert result["minutes_until_better"] == 15


def test_zero_gas_price_is_supported():
    result = build_smart_energy_advice(
        electricity_price=0.10,
        gas_price_per_m3=0.0,
        now=NOW,
    )
    assert result["gas_heat_cost_per_kwh"] == 0.0
    assert result["cheapest_now"] == "gas"
    assert result["state"] == "gas"


def test_known_load_requires_full_surplus_before_solar_surplus_state():
    result = build_smart_energy_advice(
        electricity_price=0.30,
        gas_price_per_m3=1.50,
        grid_power_w=-500,
        flexible_load_power_w=1500,
        now=NOW,
    )
    assert result["solar_surplus"] is False
    assert result["partial_surplus"] is True
    assert result["surplus_coverage_percent"] == 33.3
    assert result["required_surplus_w"] == 1500


def test_full_surplus_uses_actual_flexible_load_power():
    result = build_smart_energy_advice(
        electricity_price=0.30,
        gas_price_per_m3=1.50,
        grid_power_w=-1500,
        flexible_load_power_w=1500,
        now=NOW,
    )
    assert result["state"] == "solar_surplus"
    assert result["solar_surplus"] is True
    assert result["surplus_coverage_percent"] == 100.0


def test_partial_surplus_can_make_electric_heat_cheaper_than_gas():
    result = build_smart_energy_advice(
        electricity_price=0.30,
        gas_price_per_m3=1.50,
        grid_power_w=-1000,
        flexible_load_power_w=1500,
        now=NOW,
    )
    assert result["partial_surplus"] is True
    assert result["effective_electric_heat_cost_per_kwh"] == 0.10
    assert result["state"] == "cheap_grid"
    assert result["cheapest_now"] == "electricity"


def test_partial_surplus_can_still_leave_gas_cheaper():
    result = build_smart_energy_advice(
        electricity_price=0.42,
        gas_price_per_m3=1.50,
        grid_power_w=-500,
        flexible_load_power_w=1500,
        now=NOW,
    )
    assert result["effective_electric_heat_cost_per_kwh"] == 0.28
    assert result["state"] == "gas"
    assert result["cheapest_now"] == "gas"


def test_load_power_is_optional_and_preserves_legacy_threshold():
    result = build_smart_energy_advice(
        electricity_price=0.30,
        gas_price_per_m3=1.50,
        grid_power_w=-500,
        solar_surplus_threshold_w=500,
        now=NOW,
    )
    assert result["state"] == "solar_surplus"
    assert result["flexible_load_power_w"] is None
    assert result["required_surplus_w"] == 500


def test_flexible_load_power_must_be_positive():
    with pytest.raises(ValueError, match="Flexible load power"):
        build_smart_energy_advice(
            electricity_price=0.30,
            flexible_load_power_w=0,
            now=NOW,
        )


def test_exact_five_percent_future_threshold_can_trigger_wait_with_gas():
    prices = [PriceEntry(NOW + timedelta(minutes=15), 0.19)]
    result = build_smart_energy_advice(
        electricity_price=0.20,
        future_prices=prices,
        gas_price_per_m3=3.0,
        now=NOW,
    )
    assert result["state"] == "wait"
    assert result["next_better_price"] == 0.19


def test_missing_grid_uses_solar_production_as_explicit_fallback():
    result = build_smart_energy_advice(
        electricity_price=0.25,
        solar_power_w=900,
        flexible_load_power_w=1500,
        now=NOW,
    )
    assert result["surplus_source"] == "solar_production_fallback"
    assert result["measured_solar_surplus_w"] is None
    assert result["partial_surplus"] is True
    assert result["surplus_coverage_percent"] == 60.0


def test_missing_grid_and_solar_keep_surplus_unknown():
    result = build_smart_energy_advice(
        electricity_price=0.25,
        flexible_load_power_w=1500,
        now=NOW,
    )
    assert result["surplus_source"] == "unknown"
    assert result["measured_solar_surplus_w"] is None
    assert result["solar_surplus"] is False
    assert result["partial_surplus"] is False
    assert result["surplus_coverage_percent"] is None
    assert result["effective_electric_heat_cost_per_kwh"] == 0.25


def test_effective_cost_is_labeled_as_purchased_energy_only():
    result = build_smart_energy_advice(
        electricity_price=0.30,
        gas_price_per_m3=1.50,
        grid_power_w=-1000,
        flexible_load_power_w=1500,
        now=NOW,
    )
    assert result["cost_model"] == "purchased_energy_only"
    assert result["effective_electric_heat_cost_per_kwh"] == 0.10

def test_advisor_contract_exposes_dashboard_fields_in_both_languages():
    for language in ("en", "nl"):
        result = build_smart_energy_advice(
            electricity_price=0.30,
            future_prices=[PriceEntry(NOW + timedelta(minutes=30), 0.12)],
            gas_price_per_m3=1.50,
            grid_power_w=-500,
            flexible_load_power_w=1500,
            now=NOW,
            language=language,
        )
        assert result["state"] in {"solar_surplus", "cheap_grid", "wait", "gas", "normal"}
        assert result["recommendation"]
        assert "effective_electric_heat_cost_per_kwh" in result
        assert "gas_heat_cost_per_kwh" in result
        assert "next_better_time" in result
        assert result["surplus_source"] == "measured_grid_export"


def test_unknown_inputs_are_distinct_from_real_zero_values():
    unknown = build_smart_energy_advice(electricity_price=0.20, now=NOW)
    zero_grid = build_smart_energy_advice(electricity_price=0.20, grid_power_w=0.0, now=NOW)

    assert unknown["surplus_source"] == "unknown"
    assert unknown["measured_solar_surplus_w"] is None
    assert zero_grid["surplus_source"] == "measured_grid_export"
    assert zero_grid["measured_solar_surplus_w"] == 0.0

def test_negligible_export_is_not_partial_surplus():
    result = build_smart_energy_advice(
        electricity_price=0.30,
        gas_price_per_m3=1.50,
        grid_power_w=-5,
        flexible_load_power_w=2800,
        solar_surplus_threshold_w=500,
        now=NOW,
    )

    assert result["measured_solar_surplus_w"] == 5
    assert result["partial_surplus"] is False
    assert result["surplus_coverage_percent"] == 0.2
