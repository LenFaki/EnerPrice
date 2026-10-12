"""Validated local Forecast.Solar inputs; no HTTP, pricing or device control."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from .const import DAILY_FORECAST_DAYS

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
MAX_FORECAST_AGE = timedelta(hours=2)
MAX_MEASUREMENT_AGE = timedelta(minutes=15)
MAX_INSTALLATIONS = 8
MAX_SERIES_POINTS = 1200  # Bounded three-day responses, including quarter-hour data.
ENERGY_UNITS = {"Wh": 0.001, "kWh": 1.0}
POWER_UNITS = {"W": 1.0, "kW": 1000.0}
ROLES = {"energy_production_today": 0, "energy_production_tomorrow": 1}


@dataclass(frozen=True)
class ForecastSource:
    """Identity comes from HA's registry, never an entity's display name."""

    entity_id: str
    installation: str
    day_offset: int


def forecast_source(entity_id: str, registry_entry: Any) -> ForecastSource | None:
    if registry_entry is None or registry_entry.platform != "forecast_solar":
        return None
    entry_id = registry_entry.config_entry_id
    if not entry_id:
        return None
    for role, offset in ROLES.items():
        if registry_entry.unique_id == f"{entry_id}_{role}":
            return ForecastSource(entity_id, entry_id, offset)
    return None


def numeric_state(state: Any, device_class: str, units: dict[str, float]) -> float | None:
    """Convert known units only; missing or invalid is never zero."""
    if state is None or state.attributes.get("device_class") != device_class:
        return None
    factor = units.get(state.attributes.get("unit_of_measurement"))
    if factor is None or isinstance(state.state, bool):
        return None
    try:
        value = float(state.state) * factor
    except (ValueError, TypeError, OverflowError):
        return None
    return value if isfinite(value) else None


def state_quality(state: Any, now: datetime, max_age: timedelta) -> str | None:
    if state is None:
        return "missing"
    if state.attributes.get("restored"):
        return "restored"
    updated = state.last_updated
    if not isinstance(updated, datetime) or updated.tzinfo is None:
        return "unknown_timestamp"
    age = now.astimezone(timezone.utc) - updated.astimezone(timezone.utc)
    if age < timedelta(0) or age > max_age:
        return "stale"
    return None


def forecast_snapshot(sources: list[ForecastSource], states: dict, now: datetime) -> dict:
    """Aggregate complete configured installations only, by Amsterdam calendar day."""
    today = now.astimezone(AMSTERDAM).date()
    installations = {source.installation for source in sources}
    days = [{"date": (today + timedelta(days=i)).isoformat(), "energy_kwh": None,
             "kind": "forecast", "status": "missing", "source_count": 0}
            for i in range(DAILY_FORECAST_DAYS)]
    issues: set[str] = set()
    values: dict[tuple[str, int], float] = {}
    seen: set[tuple[str, int]] = set()
    invalid: set[tuple[str, int]] = set()
    for source in sources:
        key = (source.installation, source.day_offset)
        if key in seen:
            issues.add("duplicate_installation_day")
            invalid.add(key)
            continue
        seen.add(key)
        state = states.get(source.entity_id)
        quality = state_quality(state, now, MAX_FORECAST_AGE)
        if quality:
            issues.add(quality)
            continue
        # Relative 'today/tomorrow' states from yesterday must not roll forward.
        if state.last_updated.astimezone(AMSTERDAM).date() != today:
            issues.add("date_rollover_pending")
            continue
        value = numeric_state(state, "energy", ENERGY_UNITS)
        if value is None or value < 0:
            issues.add("invalid_energy")
            continue
        values[key] = value
    for key in invalid:
        values.pop(key, None)
    for offset, day in enumerate(days):
        applicable = [values[(entry, offset)] for entry in sorted(installations) if (entry, offset) in values]
        day["source_count"] = len(applicable)
        if installations and len(applicable) == len(installations):
            day.update(energy_kwh=round(sum(applicable), 6), status="available")
        elif applicable:
            day["status"] = "incomplete"
    if not sources:
        issues.add("no_forecast_entities")
    return {"source": "forecast_solar", "days": days, "issues": sorted(issues),
            "installation_count": len(installations), "series": [],
            "series_status": "unavailable", "quality": "partial" if any(d["energy_kwh"] is not None for d in days) else "unavailable",
            "freshness_basis": "entity_last_updated_not_api_verification",
            "solar_surplus_kwh": None}


def parse_forecast_response(response: Any, now: datetime) -> list[dict]:
    """Read the documented HA service's raw timestamp maps without inventing intervals.

    wh_period is retained as period energy. No duration is inferred from gaps
    between sunrise/sunset points; no partial series is presented as a daily total.
    """
    if not isinstance(response, dict) or not all(isinstance(response.get(key), dict) for key in ("watts", "wh_period")):
        raise ValueError("Unsupported forecast response")
    watts, energy = response["watts"], response["wh_period"]
    if len(watts) > MAX_SERIES_POINTS or len(energy) > MAX_SERIES_POINTS:
        raise ValueError("Forecast response too large")
    today = now.astimezone(AMSTERDAM).date()
    points = []
    seen = set()
    keys = set(watts) | set(energy)
    if any(not isinstance(key, str) for key in keys):
        raise ValueError("Invalid forecast timestamp")
    for raw_time in sorted(keys):
        stamp = datetime.fromisoformat(raw_time)
        if stamp.tzinfo is None:
            raise ValueError("Forecast timestamps must include an offset")
        stamp = stamp.astimezone(timezone.utc)
        if stamp in seen:
            raise ValueError("Duplicate forecast timestamp")
        seen.add(stamp)
        point = {"time": stamp.isoformat(), "power_w": None, "energy_kwh": None}
        for mapping, target, factor in ((watts, "power_w", 1), (energy, "energy_kwh", 0.001)):
            if raw_time in mapping:
                value = mapping[raw_time]
                if type(value) not in (int, float) or not isfinite(value) or value < 0:
                    raise ValueError("Invalid forecast number")
                point[target] = value * factor
        if 0 <= (stamp.astimezone(AMSTERDAM).date() - today).days < DAILY_FORECAST_DAYS:
            points.append(point)
    return sorted(points, key=lambda point: point["time"])
