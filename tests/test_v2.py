from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.nl_day_ahead_prices.advisor import build_price_advice
from custom_components.nl_day_ahead_prices.dashboard import (
    generate_automation_yaml,
    generate_dashboard_yaml,
)
from custom_components.nl_day_ahead_prices.models import PriceEntry
from custom_components.nl_day_ahead_prices.planning import (
    plan_appliance,
    plan_battery,
    plan_ev_charging,
    plan_export,
    plan_heating,
)
from custom_components.nl_day_ahead_prices.scoring import (
    calculate_day_score,
    calculate_price_score,
)
from custom_components.nl_day_ahead_prices.supplier_profiles import (
    PROFILE_FILE,
    normalize_supplier_profile,
    supplier_profile_to_dict,
)


def _prices(values: list[float], minutes: int = 60, start: datetime | None = None) -> list[PriceEntry]:
    start = start or datetime(2026, 7, 9, tzinfo=timezone.utc)
    return [
        PriceEntry(start + timedelta(minutes=index * minutes), value)
        for index, value in enumerate(values)
    ]


def test_price_score_positive_prices() -> None:
    result = calculate_price_score(0.10, _prices([0.10, 0.20, 0.30]))
    assert result["score"] == 100
    assert result["score_label"] == "excellent"
    assert result["percentile"] == pytest.approx(33.3)


def test_price_score_negative_prices() -> None:
    prices = _prices([-0.10, -0.05, 0.10])
    assert calculate_price_score(-0.10, prices)["score"] == 100
    assert calculate_price_score(0.10, prices)["score"] == 0


def test_day_score_contains_blocks_and_minutes() -> None:
    result = calculate_day_score(_prices([-0.05, 0.10, 0.20, 0.40]))
    assert result["state"] == "excellent"
    assert result["negative_price_minutes"] == 60
    assert result["cheapest_block_1h"]["average_price"] == -0.05
    assert result["expensive_block_1h"]["average_price"] == 0.4


@pytest.mark.parametrize(
    ("score", "expected"),
    [(95, "excellent"), (75, "good"), (50, "neutral"), (30, "avoid"), (5, "critical")],
)
def test_advisor_states(score: int, expected: str) -> None:
    result = build_price_advice(
        current_price=0.1,
        all_in_price=0.2,
        score={"score": score},
        rating="normal",
        trend="stable",
        volatility="low",
    )
    assert result["state"] == expected
    assert result["recommendation"]


def test_advisor_uses_dutch_user_facing_text() -> None:
    result = build_price_advice(
        current_price=0.1,
        all_in_price=0.2,
        score={"score": 95},
        rating="very_cheap",
        trend="stable",
        volatility="low",
        language="nl",
    )
    assert result["title"] == "Zeer goedkoop moment"
    assert "grootverbruikers" in result["recommendation"]
    assert "EV laden" in result["best_actions"]



def test_advisor_exposes_next_better_price_context() -> None:
    now = datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc)
    prices = _prices([0.30, 0.29, 0.20, 0.10], start=now)
    result = build_price_advice(
        current_price=0.20,
        all_in_price=0.30,
        score={"score": 30},
        rating="expensive",
        trend="falling",
        volatility="moderate",
        language="nl",
        prices=prices,
        now=now,
    )
    assert result["next_better_time"] == prices[2].time
    assert result["next_better_price"] == pytest.approx(0.20)
    assert result["minutes_until_better"] == 120
    assert result["savings_percent"] == pytest.approx(33.3)
    assert result["best_upcoming_time"] == prices[3].time
    assert "Wacht" in result["summary"]


def test_advisor_summary_recommends_using_good_price_now() -> None:
    now = datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc)
    result = build_price_advice(
        current_price=0.05,
        all_in_price=0.10,
        score={"score": 95},
        rating="very_cheap",
        trend="stable",
        volatility="low",
        language="nl",
        prices=_prices([0.10, 0.20, 0.30], start=now),
        now=now,
    )
    assert result["state"] == "excellent"
    assert result["next_better_time"] is None
    assert "Goed moment" in result["summary"]


def test_advisor_handles_negative_prices_without_percentage_math() -> None:
    now = datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc)
    prices = _prices([-0.02, -0.03, -0.04], minutes=15, start=now)
    result = build_price_advice(
        current_price=-0.02,
        all_in_price=-0.02,
        score={"score": 95},
        rating="very_cheap",
        trend="falling",
        volatility="moderate",
        language="nl",
        prices=prices,
        now=now,
    )
    assert result["state"] == "excellent"
    assert result["next_better_time"] == prices[1].time
    assert result["next_better_price"] == pytest.approx(-0.03)
    assert result["minutes_until_better"] == 15
    assert result["savings_percent"] is None
    assert "Goed moment" in result["summary"]


def test_advisor_uses_quarter_hour_wait_time() -> None:
    now = datetime(2026, 7, 9, 10, 7, tzinfo=timezone.utc)
    prices = _prices([0.30, 0.20, 0.19], minutes=15, start=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc))
    result = build_price_advice(
        current_price=0.30,
        all_in_price=0.30,
        score={"score": 30},
        rating="expensive",
        trend="falling",
        volatility="low",
        language="nl",
        prices=prices,
        now=now,
    )
    assert result["next_better_time"] == prices[1].time
    assert result["minutes_until_better"] == 8
    assert result["savings_percent"] == pytest.approx(33.3)

def test_ev_planner_consecutive_hourly() -> None:
    prices = _prices([0.4, 0.1, 0.2, 0.5])
    result = plan_ev_charging(
        prices,
        target_energy_kwh=4,
        max_power_kw=2,
        earliest_start=prices[0].time,
        deadline=prices[-1].time + timedelta(hours=1),
    )
    assert result["recommended_start"] == prices[1].time
    assert result["duration_minutes"] == 120
    assert result["estimated_cost"] == pytest.approx(0.6)


def test_ev_planner_split_quarter_hour() -> None:
    prices = _prices([0.5, 0.1, 0.5, 0.2, 0.5], 15)
    result = plan_ev_charging(
        prices,
        target_energy_kwh=1,
        max_power_kw=2,
        earliest_start=prices[0].time,
        deadline=prices[-1].time + timedelta(minutes=15),
        require_consecutive=False,
        minimum_session_minutes=15,
        allow_split_sessions=True,
    )
    selected = [entry["price"] for entry in result["selected_intervals"]]
    assert selected == [0.1, 0.2]
    assert result["duration_minutes"] == 30


def test_boiler_planner_prefers_before_peak() -> None:
    prices = _prices([0.2, 0.1, 0.2, 0.8, 0.05])
    result = plan_heating(
        prices,
        duration_minutes=120,
        earliest_start=prices[0].time,
        deadline=prices[-1].time + timedelta(hours=1),
        prefer_before_peak=True,
    )
    assert result["recommended_start"] == prices[0].time
    assert result["recommended_end"] == prices[2].time


def test_battery_arbitrage_respects_efficiency() -> None:
    result = plan_battery(
        _prices([0.10, 0.10, 0.20, 0.50, 0.50, 0.20]),
        battery_capacity_kwh=10,
        current_soc_percent=50,
        min_soc_percent=10,
        max_soc_percent=90,
        charge_power_kw=2,
        discharge_power_kw=2,
        roundtrip_efficiency_percent=90,
    )
    assert result["arbitrage_opportunity"] is True
    assert result["expected_savings"] > 0
    assert result["charge_windows"]
    assert result["discharge_windows"]


def test_export_planner_selects_highest_price() -> None:
    prices = _prices([0.1, 0.4, 0.2])
    result = plan_export(prices, expected_export_kwh=3, duration_minutes=60)
    assert result["best_export_start"] == prices[1].time
    assert result["average_sell_price"] == 0.4
    assert result["estimated_revenue"] == pytest.approx(1.2)


def test_appliance_planner_returns_automation() -> None:
    prices = _prices([0.4, 0.1, 0.1, 0.5])
    result = plan_appliance(
        prices,
        appliance_name="Washing machine",
        duration_minutes=120,
        earliest_start=prices[0].time,
        deadline=prices[-1].time + timedelta(hours=1),
        energy_kwh=2,
    )
    assert result["recommended_start"] == prices[1].time
    assert result["estimated_cost"] == pytest.approx(0.2)
    assert "switch.washing_machine" in result["automation_example"]


def test_dashboard_yaml_generation() -> None:
    generated = generate_dashboard_yaml("full", include_ev_planner=True)
    assert "title: EnerPrice" in generated
    assert "type: sections" in generated
    assert "EnerPrice Advisor" in generated
    assert "next_better_time" in generated
    assert "custom:apexcharts-card" in generated
    assert "sensor.nl_day_ahead_price_advisor" in generated



def test_dashboard_yaml_accepts_resolved_entity_ids() -> None:
    generated = generate_dashboard_yaml(
        "compact",
        entity_ids={
            "price_advisor": "sensor.meterkast_price_advisor",
            "price_score": "sensor.meterkast_price_score",
            "current_all_in_price": "sensor.meterkast_current_all_in_price",
        },
    )
    assert "sensor.meterkast_price_advisor" in generated
    assert "sensor.meterkast_price_score" in generated
    assert "sensor.meterkast_current_all_in_price" in generated
    assert "states('sensor.meterkast_price_advisor')" in generated

def test_compact_dashboard_uses_native_advisor_blocks_without_chart() -> None:
    generated = generate_dashboard_yaml("compact")
    assert "type: tile" in generated
    assert "type: markdown" in generated
    assert "custom:apexcharts-card" not in generated
    assert "max_columns: 1" in generated


def test_automation_yaml_generation() -> None:
    generated = generate_automation_yaml(
        "boiler_best_period",
        "switch.boiler",
        duration_minutes=120,
    )
    assert "binary_sensor.nl_day_ahead_prices_best_price_period" in generated
    assert "switch.boiler" in generated


def test_supplier_profile_v1_migrates_to_v2() -> None:
    profile = normalize_supplier_profile(
        {
            "name": "Legacy",
            "monthly_fee_electricity": 5,
            "purchase_fee_electricity": 0.02,
            "sell_fee_electricity": 0.01,
            "price_resolution": "hourly",
        }
    )
    serialized = supplier_profile_to_dict(profile)
    assert profile.profile_version == 2
    assert serialized["fixed_monthly_fee_electricity"] == 5
    assert serialized["purchase_fee_import"] == 0.02
    assert serialized["purchase_fee_export"] == 0.01


def test_supplier_profile_v2_without_legacy_fields() -> None:
    profile = normalize_supplier_profile(
        {
            "name": "V2 supplier",
            "fixed_monthly_fee_electricity": 6,
            "purchase_fee_import": 0.03,
            "purchase_fee_export": 0.01,
            "default_settlement_resolution": "quarter_hour",
            "supports_quarter_hour_prices": True,
        }
    )
    assert profile.monthly_fee_electricity == 6
    assert profile.purchase_fee_electricity == 0.03
    assert profile.default_settlement_resolution == "quarter_hour"
    assert profile.price_resolution == "quarter_hour"


def test_bundled_supplier_profiles_have_v2_metadata() -> None:
    profiles = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    required = {
        "supports_hourly_prices",
        "supports_quarter_hour_prices",
        "default_settlement_resolution",
        "fixed_monthly_fee_electricity",
        "purchase_fee_import",
        "purchase_fee_export",
        "feed_in_fee",
        "notes",
        "last_verified",
        "source_url",
    }
    assert profiles
    assert all(required <= profile.keys() for profile in profiles.values())


def test_english_and_dutch_ui_translations_have_equal_coverage() -> None:
    translations = PROFILE_FILE.parent / "translations"
    english = json.loads((translations / "en.json").read_text(encoding="utf-8"))
    dutch = json.loads((translations / "nl.json").read_text(encoding="utf-8"))

    def keys(value, prefix: str = "") -> set[str]:
        if not isinstance(value, dict):
            return {prefix}
        return set().union(
            *(keys(child, f"{prefix}.{key}" if prefix else key) for key, child in value.items())
        )

    assert keys(english) == keys(dutch)
    assert set(dutch["services"]) == {
        "get_daily_forecast",
        "export_chart_data",
        "generate_apexcharts_config",
        "find_best_charging_window",
        "find_best_heating_window",
        "find_battery_strategy",
        "find_best_export_window",
        "find_best_appliance_window",
        "generate_dashboard_yaml",
        "generate_automation_yaml",
    }


@pytest.mark.parametrize(
    ("local_date", "expected"),
    [((2026, 3, 29), 23), ((2026, 10, 25), 25)],
)
def test_ev_planner_handles_dst_days(local_date: tuple[int, int, int], expected: int) -> None:
    zone = ZoneInfo("Europe/Amsterdam")
    day = datetime(*local_date, tzinfo=zone).date()
    cursor = datetime(*local_date, tzinfo=zone).astimezone(timezone.utc)
    prices = []
    while cursor.astimezone(zone).date() == day:
        prices.append(PriceEntry(cursor.astimezone(zone), 0.1))
        cursor += timedelta(hours=1)
    assert len(prices) == expected
    result = plan_ev_charging(
        prices,
        target_energy_kwh=2,
        max_power_kw=2,
        earliest_start=prices[0].time,
        deadline=prices[-1].time + timedelta(hours=1),
    )
    assert result["duration_minutes"] == 60


def test_ev_planner_can_span_repeated_dst_hour() -> None:
    zone = ZoneInfo("Europe/Amsterdam")
    cursor = datetime(2026, 10, 24, 22, tzinfo=timezone.utc)
    prices = [
        PriceEntry((cursor + timedelta(hours=index)).astimezone(zone), 0.1 if index in {2, 3} else 0.5)
        for index in range(25)
    ]
    result = plan_ev_charging(
        prices,
        target_energy_kwh=4,
        max_power_kw=2,
        earliest_start=prices[0].time,
        deadline=prices[-1].time + timedelta(hours=1),
    )
    assert result["recommended_start"].astimezone(timezone.utc) == prices[2].time.astimezone(timezone.utc)
    assert result["recommended_end"].astimezone(timezone.utc) == prices[4].time.astimezone(timezone.utc)
