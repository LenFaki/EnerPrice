"""Conservative energy evidence and bounded persistence regressions."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest

from custom_components.nl_day_ahead_prices.daily_tracking import (
    ROLES,
    empty_history,
    new_day,
    performance_summary,
    restore_history,
    storage_snapshot,
    update_history,
)
from custom_components.nl_day_ahead_prices.solar_forecast import AMSTERDAM

START = datetime(2026, 10, 12, tzinfo=AMSTERDAM).astimezone(timezone.utc)


def forecast(now, reading=100, **overrides):
    result = {"measurements": [
        {"role": role, "entity_id": f"sensor.{role}", "kind": "measured", "state_class": "total_increasing",
         "value": reading, "last_updated": now.isoformat()} for role in ROLES
    ], "days": [{"date": now.astimezone(AMSTERDAM).date().isoformat(), "energy_kwh": 12}]}
    result.update(overrides)
    return result


def whole_day(*, start=START, hours=24, mutate=None, restart=False):
    history = empty_history()
    for minute in range(0, hours * 60 + 1, 5):
        now = start + timedelta(minutes=minute)
        data = forecast(now, 100 + minute / 60)
        if mutate:
            mutate(minute, data)
        update_history(history, now, data, {}, 30)
        if restart and minute == 720:
            history = restore_history(storage_snapshot(history), now.astimezone(AMSTERDAM).date(), 30)
    return history


@pytest.mark.parametrize("restart", [False, True])
def test_complete_cumulative_day_compares_energy_not_realized_savings(restart):
    history = whole_day(restart=restart)
    summary = performance_summary(history, date(2026, 10, 13))
    assert summary["metrics"]["solar_energy"] == {"predicted_kwh": 12, "actual_kwh": None if restart else 24,
                                                "error_kwh": None if restart else 12, "quality": "incomplete" if restart else "comparable"}
    assert summary["metrics"]["import_energy"]["actual_kwh"] == (None if restart else 24)
    assert summary["metrics"]["import_energy"]["predicted_kwh"] is None
    assert summary["realized_savings_eur"] is None
    assert "days" not in summary


@pytest.mark.parametrize("start,hours", [(datetime(2026, 3, 29, tzinfo=AMSTERDAM), 23), (datetime(2026, 10, 25, tzinfo=AMSTERDAM), 25)])
def test_dst_actual_totals_follow_absolute_calendar_day(start, hours):
    history = whole_day(start=start.astimezone(timezone.utc), hours=hours)
    assert history["days"][0]["actual"] == dict.fromkeys(ROLES, hours)


@pytest.mark.parametrize("fault", ["stale", "missing", "reset", "different_meter", "wrong_class", "nan", "bool"])
def test_bad_measurement_never_becomes_zero_or_valid_day(fault):
    def mutate(minute, data):
        if minute != 60:
            return
        item = data["measurements"][0]
        if fault == "stale":
            item["last_updated"] = (START - timedelta(hours=1)).isoformat()
        elif fault == "missing":
            item.update(kind="unknown", value=None)
        elif fault == "reset":
            item["value"] = 0
        elif fault == "different_meter":
            item["entity_id"] = "sensor.replacement"
        elif fault == "wrong_class":
            item["state_class"] = "measurement"
        elif fault == "nan":
            item["value"] = float("nan")
        else:
            item["value"] = True
    history = whole_day(mutate=mutate)
    assert history["days"][0]["actual"]["solar_energy"] is None
    assert history["days"][0]["actual"]["import_energy"] == 24


def test_missing_midnight_start_long_restart_gap_and_late_end_are_unknown():
    history = empty_history()
    update_history(history, START + timedelta(hours=1), forecast(START + timedelta(hours=1)), {}, 30)
    update_history(history, START + timedelta(days=1), forecast(START + timedelta(days=1), 150), {}, 30)
    assert history["days"][0]["actual"] == dict.fromkeys(ROLES)
    history = empty_history()
    update_history(history, START, forecast(START), {}, 30)
    update_history(history, START + timedelta(days=1, minutes=2), forecast(START + timedelta(days=1, minutes=2), 150), {}, 30)
    assert history["days"][0]["actual"] == dict.fromkeys(ROLES)


def test_finalized_boundary_not_overwritten_by_second_sample_or_restart():
    history = whole_day()
    copy = deepcopy(history["days"][0])
    now = START + timedelta(days=1, minutes=1)
    update_history(history, now, forecast(now, 125), {}, 30)
    assert history["days"][0] == copy


def test_freshly_reported_unchanged_counters_prove_zero_not_missing_values():
    def unchanged(minute, data):
        for item in data["measurements"]:
            item.update(value=100, observed_at=item["last_updated"], last_updated=(START - timedelta(days=1)).isoformat())
    history = whole_day(mutate=unchanged)
    assert history["days"][0]["actual"] == dict.fromkeys(ROLES, 0)


def test_forecast_frozen_morning_only_and_potential_alternatives_not_summed():
    history = empty_history()
    advice = {"advice": [{"start": START.isoformat(), "estimated_impact_eur": value} for value in (0.5, 0.7, -0.2)]}
    update_history(history, START, forecast(START), advice, 30)
    update_history(history, START + timedelta(hours=10), forecast(START, days=[{"date": "2026-10-12", "energy_kwh": 25}]), {}, 30)
    assert history["days"][0]["predicted"]["solar_energy"] == 12
    assert history["days"][0]["potential_savings_eur"] == 0.7
    late = START + timedelta(hours=13)
    other = empty_history()
    update_history(other, late, forecast(late), advice, 30)
    assert other["days"][0]["predicted"]["solar_energy"] is None
    assert other["days"][0]["potential_savings_eur"] is None


def test_grid_prediction_requires_full_day_series_and_constant_load_assumption():
    history = empty_history()
    data = forecast(START, installation_count=1, series_status="available", series=[{"installation": "roof", "points": [
        {"time": (START + timedelta(hours=i)).isoformat(), "power_w": 1000} for i in range(25)
    ]}])
    update_history(history, START, data, {"assumed_household_load_w": 1500}, 30)
    assert history["days"][0]["predicted"]["import_energy"] == pytest.approx(12)
    assert history["days"][0]["predicted"]["export_energy"] == pytest.approx(0)
    data["series"][0]["points"].pop(10)
    other = empty_history()
    update_history(other, START, data, {"assumed_household_load_w": 1500}, 30)
    assert other["days"][0]["predicted"]["import_energy"] is None


def test_late_morning_power_series_does_not_lose_grid_capture_readiness():
    history = empty_history()
    update_history(history, START, forecast(START), {}, 30)
    assert history["days"][0]["grid_captured_at"] is None
    now = START + timedelta(minutes=5)
    data = forecast(now, installation_count=1, series_status="available", series=[{"installation": "roof", "points": [
        {"time": (START + timedelta(hours=i)).isoformat(), "power_w": 1000} for i in range(25)
    ]}])
    update_history(history, now, data, {"assumed_household_load_w": 500}, 30)
    assert history["days"][0]["predicted"]["export_energy"] == pytest.approx(12)
    assert history["days"][0]["grid_captured_at"] == now.isoformat()
    assert history["days"][0]["captured_at"] == START.isoformat()


def test_retention_bounded_by_days_including_current_and_preserves_claim():
    history = empty_history()
    history.update(notification_date="2026-10-13", notification_result="claimed")
    for offset in range(200):
        now = START + timedelta(days=offset, hours=12)
        update_history(history, now, {}, {}, 90)
    assert len(history["days"]) == 91
    today = now.astimezone(AMSTERDAM).date()
    smaller = restore_history(history, today, 1)
    assert len(smaller["days"]) == 2
    assert smaller["notification_date"] == "2026-10-13"
    snapshot = storage_snapshot(smaller)
    smaller["days"].clear()
    assert len(snapshot["days"]) == 2


@pytest.mark.parametrize("payload", [{}, {"schema": 1, "days": []}, {"schema": 2, "days": []}, {"schema": True, "days": []},
                                    {**empty_history(), "notification_result": "sent"},
                                    {**empty_history(), "notification_date": "20261012", "notification_result": "sent"},
                                    {"schema": 1, "days": [None]}, {"schema": 1, "days": [new_day(date(2026, 10, 12))] * 92}])
def test_corrupt_or_future_history_fails_closed(payload):
    with pytest.raises((ValueError, TypeError)):
        restore_history(payload, date(2026, 10, 12), 30)


def test_no_storage_is_safe_legacy_migration():
    assert restore_history(None, date(2026, 10, 12), 30) == empty_history()
