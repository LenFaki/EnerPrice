"""Bounded daily evidence, independent of notification delivery and Home Assistant."""

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite

from .solar_forecast import AMSTERDAM

MAX_RETENTION_DAYS = 90
ROLES = ("solar_energy", "import_energy", "export_energy")


def finite(value):
    return type(value) in (int, float) and isfinite(value)


def empty_history():
    return {"schema": 1, "notification_date": None, "notification_result": None, "days": []}


def restore_history(value, today, retention):
    """Fail closed for unknown/corrupt storage, never recover a lost delivery claim."""
    if value is None:
        return empty_history()
    if not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] != 1 or not isinstance(value.get("days"), list):
        raise ValueError("Unsupported Daily Advisor storage")
    if "notification_date" not in value or "notification_result" not in value:
        raise ValueError("Missing notification claim fields")
    claim = value.get("notification_date")
    if claim is not None:
        if date.fromisoformat(claim).isoformat() != claim:
            raise ValueError("Invalid claim date")
    if value.get("notification_result") not in (None, "claimed", "sent", "failed"):
        raise ValueError("Invalid notification result")
    if (claim is None) != (value.get("notification_result") is None):
        raise ValueError("Incomplete notification claim")
    if len(value["days"]) > MAX_RETENTION_DAYS + 1:
        raise ValueError("Unbounded history")
    result = empty_history()
    result.update(notification_date=claim, notification_result=value.get("notification_result"))
    seen = set()
    for row in value["days"]:
        if not isinstance(row, dict):
            raise ValueError("Invalid history row")
        day = date.fromisoformat(row["date"])
        if day in seen:
            raise ValueError("Duplicate history date")
        seen.add(day)
        # Only our fixed schema is retained; unknown payloads cannot grow storage.
        clean = new_day(day)
        if type(row.get("finalized")) is not bool:
            raise ValueError("Invalid finalization flag")
        clean["finalized"] = row["finalized"]
        for key in ("captured_at", "grid_captured_at", "potential_captured_at"):
            if row.get(key) is not None:
                if datetime.fromisoformat(row[key]).tzinfo is None:
                    raise ValueError("Naive history timestamp")
                clean[key] = row[key]
        for key in ("predicted", "actual"):
            for role in ROLES:
                number = row[key][role]
                if number is not None and (not finite(number) or number < 0):
                    raise ValueError("Invalid history energy")
                clean[key][role] = number
        number = row.get("potential_savings_eur")
        if number is not None and not finite(number):
            raise ValueError("Invalid potential impact")
        clean["potential_savings_eur"] = number
        for role in ROLES:
            meter = row.get("meters", {}).get(role)
            if meter is None:
                continue
            if not isinstance(meter.get("entity_id"), str) or len(meter["entity_id"]) > 255:
                raise ValueError("Invalid meter identity")
            if any(not finite(meter.get(key)) or meter[key] < 0 for key in ("start", "last")):
                raise ValueError("Invalid meter reading")
            if type(meter.get("valid")) is not bool or not finite(meter.get("observed")):
                raise ValueError("Invalid meter continuity")
            clean["meters"][role] = {key: meter[key] for key in ("entity_id", "start", "last", "valid", "observed")}
            # A process gap can hide a reset or invalid sample. Never restore proof
            # of continuous observation for a day that was not finalized yet.
            if not clean["finalized"]:
                clean["meters"][role]["valid"] = False
        result["days"].append(clean)
    prune_history(result, today, retention)
    return result


def new_day(day):
    return {"date": day.isoformat(), "finalized": False, "captured_at": None, "grid_captured_at": None, "potential_captured_at": None,
            "predicted": dict.fromkeys(ROLES), "actual": dict.fromkeys(ROLES), "meters": {},
            "potential_savings_eur": None}


def prune_history(history, today, retention):
    retention = max(1, min(MAX_RETENTION_DAYS, int(retention)))
    cutoff = today - timedelta(days=retention)
    history["days"] = sorted(
        [row for row in history["days"] if cutoff <= date.fromisoformat(row["date"]) <= today],
        key=lambda row: row["date"],
    )[-(retention + 1):]


def _counter(measurement, now, boundary=False):
    """Only fresh cumulative meters; daily-reset counters are not interchangeable."""
    if not measurement or measurement.get("kind") != "measured" or measurement.get("state_class") != "total_increasing":
        return None
    value = measurement.get("value")
    stamp = measurement.get("observed_at", measurement.get("last_updated"))
    if not finite(value) or value < 0 or not stamp:
        return None
    updated = datetime.fromisoformat(stamp)
    if updated.tzinfo is None or not 0 <= (now - updated).total_seconds() <= (90 if boundary else 900):
        return None
    return value


def update_history(history, now, forecast, advice, retention):
    """Capture a fixed forecast and monotonic meter evidence, never a guessed saving."""
    now = now.astimezone(timezone.utc)
    local = now.astimezone(AMSTERDAM)
    today = local.date()
    start = datetime.combine(today, time.min, AMSTERDAM).astimezone(timezone.utc)
    boundary = 0 <= (now - start).total_seconds() <= 90
    measurements = {item["role"]: item for item in forecast.get("measurements", [])}
    yesterday = (today - timedelta(days=1)).isoformat()
    for row in history["days"]:
        if row["date"] >= today.isoformat() or row["finalized"]:
            continue
        row["finalized"] = True
        if row["date"] != yesterday or not boundary:
            continue
        for role, meter in row["meters"].items():
            reading = _counter(measurements.get(role), now, boundary=True)
            same = measurements.get(role, {}).get("entity_id") == meter["entity_id"]
            if meter["valid"] and same and reading is not None and reading >= meter["last"] and 0 <= now.timestamp() - meter["observed"] <= 1200:
                row["actual"][role] = reading - meter["start"]
            else:
                row["actual"][role] = None
    row = next((row for row in history["days"] if row["date"] == today.isoformat()), None)
    if row is None:
        row = new_day(today)
        history["days"].append(row)
    for role in ROLES:
        measurement = measurements.get(role)
        value = _counter(measurement, now)
        meter = row["meters"].get(role)
        if meter is None:
            if boundary and _counter(measurement, now, boundary=True) is not None:
                row["meters"][role] = {"entity_id": measurement["entity_id"], "start": value, "last": value,
                                       "valid": True, "observed": now.timestamp()}
            continue
        if value is None or measurement.get("entity_id") != meter["entity_id"] or value < meter["last"] or not 0 <= now.timestamp() - meter["observed"] <= 1200:
            meter["valid"] = False
        if value is not None:
            meter["last"] = value
        meter["observed"] = now.timestamp()
    # Freeze the first usable morning forecast. Late data is not a prior prediction.
    solar = next((day.get("energy_kwh") for day in forecast.get("days", []) if day["date"] == today.isoformat()), None)
    if row["captured_at"] is None and local.hour < 12 and finite(solar) and solar >= 0:
        row["captured_at"] = now.isoformat()
        row["predicted"]["solar_energy"] = solar
    if row["grid_captured_at"] is None and local.hour < 12:
        predicted = _grid_prediction(forecast, advice, start, today)
        if predicted["import_energy"] is not None:
            row["grid_captured_at"] = now.isoformat()
            row["predicted"].update(predicted)
    if row["potential_captured_at"] is None and local.hour < 12:
        windows = [item for item in advice.get("advice", [])
                   if datetime.fromisoformat(item["start"]).astimezone(AMSTERDAM).date() == today
                   and finite(item.get("estimated_impact_eur"))]
        if windows:
            # Alternatives overlap: never sum them or treat appliance execution as proven.
            row["potential_savings_eur"] = max(item["estimated_impact_eur"] for item in windows)
            row["potential_captured_at"] = now.isoformat()
    prune_history(history, today, retention)


def _grid_prediction(forecast, advice, start, today):
    """Reuse PR2's integration model only with full-day power coverage and a baseline."""
    from .smart_energy import _power_series, _solar_load_energy

    result = {"import_energy": None, "export_energy": None}
    baseline = advice.get("assumed_household_load_w")
    series = _power_series(forecast)
    if not finite(baseline) or baseline < 0 or not series:
        return result
    end = datetime.combine(today + timedelta(days=1), time.min, AMSTERDAM).astimezone(timezone.utc)
    peak = sum(max(watts for _, watts in points) for points in series)
    produced = _solar_load_energy(series, start.timestamp(), end.timestamp(), 0, peak)
    matched = _solar_load_energy(series, start.timestamp(), end.timestamp(), 0, baseline)
    if produced is not None and matched is not None:
        result.update(import_energy=max(0, baseline * (end - start).total_seconds() / 3600000 - matched),
                      export_energy=max(0, produced - matched))
    return result


def performance_summary(history, today):
    """One finalized date in attributes; history stays in bounded private storage."""
    rows = [row for row in history["days"] if date.fromisoformat(row["date"]) < today]
    if not rows:
        return {"state": "collecting", "date": None, "metrics": {}, "potential_savings_eur": None,
                "realized_savings_eur": None, "realized_savings_status": "no_verified_counterfactual"}
    row = rows[-1]
    metrics = {}
    for role in ROLES:
        predicted, actual = row["predicted"][role], row["actual"][role]
        metrics[role] = {"predicted_kwh": predicted, "actual_kwh": actual,
                         "error_kwh": actual - predicted if actual is not None and predicted is not None else None,
                         "quality": "comparable" if actual is not None and predicted is not None else "incomplete"}
    return {"state": "comparable" if all(item["quality"] == "comparable" for item in metrics.values()) else "incomplete",
            "date": row["date"], "metrics": metrics, "forecast_captured_at": row["captured_at"],
            "grid_forecast_captured_at": row["grid_captured_at"],
            "grid_prediction_model": "constant_household_load_scenario", "potential_savings_eur": row["potential_savings_eur"],
            "realized_savings_eur": None, "realized_savings_status": "no_verified_counterfactual"}


def storage_snapshot(history):
    """Prevent asynchronous serialization from seeing subsequent in-memory mutations."""
    return deepcopy(history)
