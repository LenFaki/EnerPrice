"""Generic smart-energy and heat-source advice for EnerPrice."""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, time, timedelta, timezone
from functools import cache
from math import ceil, isfinite
from typing import Any

from .models import PriceEntry
from .optimizer import optimize_intervals
from .solar_forecast import AMSTERDAM

DEFAULT_GAS_KWH_PER_M3 = 9.769


def build_smart_energy_advice(
    *,
    electricity_price: float | None,
    future_prices: list[Any] | None = None,
    now: datetime | None = None,
    solar_power_w: float | None = None,
    grid_power_w: float | None = None,
    gas_price_per_m3: float | None = None,
    electric_efficiency: float = 1.0,
    gas_efficiency: float = 0.90,
    gas_kwh_per_m3: float = DEFAULT_GAS_KWH_PER_M3,
    solar_surplus_threshold_w: float = 500.0,
    flexible_load_power_w: float | None = None,
    language: str = "en",
    daily_context: dict | None = None,
) -> dict[str, Any]:
    """Compare solar, grid electricity, gas and waiting for flexible heat loads."""
    if electric_efficiency <= 0 or gas_efficiency <= 0 or gas_kwh_per_m3 <= 0:
        raise ValueError("Efficiencies and gas energy content must be positive")
    if flexible_load_power_w is not None and flexible_load_power_w <= 0:
        raise ValueError("Flexible load power must be positive")

    is_nl = language.lower().startswith("nl")
    solar_production = solar_power_w is not None and solar_power_w > 0
    # Positive grid power means import. When grid data is available, actual export is
    # the most reliable definition of surplus. Solar production alone is used only
    # when no grid meter was supplied.
    measured_surplus = max(0.0, -grid_power_w) if grid_power_w is not None else None
    if measured_surplus is not None:
        available_surplus = measured_surplus
        surplus_source = "measured_grid_export"
    elif solar_power_w is not None:
        available_surplus = max(0.0, solar_power_w)
        surplus_source = "solar_production_fallback"
    else:
        available_surplus = None
        surplus_source = "unknown"
    required_surplus = flexible_load_power_w if flexible_load_power_w is not None else solar_surplus_threshold_w
    solar_surplus = available_surplus is not None and available_surplus >= required_surplus
    partial_surplus = (
        flexible_load_power_w is not None
        and available_surplus is not None
        and available_surplus >= solar_surplus_threshold_w
        and available_surplus < flexible_load_power_w
    )
    surplus_coverage = (
        min(100.0, available_surplus / flexible_load_power_w * 100)
        if flexible_load_power_w is not None and available_surplus is not None
        else None
    )

    electric_heat_cost = (
        electricity_price / electric_efficiency if electricity_price is not None else None
    )
    # When the flexible load demand is known, measured surplus can cover part of
    # that demand. Only the remaining grid share is priced at the current all-in
    # electricity tariff. This is intentionally advisory and assumes available
    # surplus has no additional purchase cost.
    grid_share = (
        max(0.0, flexible_load_power_w - available_surplus) / flexible_load_power_w
        if flexible_load_power_w is not None and available_surplus is not None
        else 1.0
    )
    effective_electric_heat_cost = (
        electric_heat_cost * grid_share if electric_heat_cost is not None else None
    )
    gas_heat_cost = (
        gas_price_per_m3 / (gas_kwh_per_m3 * gas_efficiency)
        if gas_price_per_m3 is not None
        else None
    )
    timing = _future_context(electricity_price, future_prices or [], now, electric_efficiency)

    if solar_surplus:
        state = "solar_surplus"
        recommendation = (
            "Gebruik eigen zonnestroom voor flexibel elektrisch verwarmen."
            if is_nl else
            "Use available solar surplus for flexible electric heating."
        )
    elif effective_electric_heat_cost is not None and gas_heat_cost is not None:
        if not partial_surplus and timing["future_electric_heat_cost"] is not None and timing["future_electric_heat_cost"] <= min(
            effective_electric_heat_cost, gas_heat_cost
        ) * 0.95:
            state = "wait"
            recommendation = (
                f"Wacht indien mogelijk ongeveer {timing['minutes_until_better']} min; elektrisch verwarmen wordt dan naar verwachting goedkoper dan nu."
                if is_nl else
                f"Wait about {timing['minutes_until_better']} min if practical; electric heating is expected to be cheaper then."
            )
        elif effective_electric_heat_cost <= gas_heat_cost:
            state = "cheap_grid"
            if partial_surplus:
                recommendation = (
                    f"Gebruik het gedeeltelijke energieoverschot ({surplus_coverage:.0f}%) en vul de rest aan met netstroom; de ingekochte energie is in dit model nu goedkoper dan gas."
                    if is_nl else
                    f"Use the partial energy surplus ({surplus_coverage:.0f}%) and supply the remainder from the grid; purchased energy is currently cheaper than gas in this model."
                )
            else:
                recommendation = (
                    "Elektrisch verwarmen vanaf het net is nu goedkoper dan verwarmen met gas."
                    if is_nl else
                    "Grid-electric heating is currently cheaper than gas heating."
                )
        else:
            state = "gas"
            recommendation = (
                "Verwarmen met gas is nu goedkoper dan elektrisch verwarmen vanaf het net."
                if is_nl else
                "Gas heating is currently cheaper than grid-electric heating."
            )
    elif effective_electric_heat_cost is not None:
        state = "cheap_grid" if partial_surplus or timing["next_better_price"] is None else "wait"
        recommendation = (
            "Elektriciteitsadvies beschikbaar; configureer een gasprijssensor voor een warmtebronvergelijking."
            if is_nl else
            "Electricity advice is available; configure a gas-price sensor for heat-source comparison."
        )
    else:
        state = "normal"
        recommendation = (
            "Onvoldoende gegevens voor een warmtebronadvies."
            if is_nl else
            "Not enough data is available for heat-source advice."
        )

    savings = None
    if effective_electric_heat_cost is not None and gas_heat_cost is not None and max(effective_electric_heat_cost, gas_heat_cost) > 0:
        savings = abs(effective_electric_heat_cost - gas_heat_cost) / max(effective_electric_heat_cost, gas_heat_cost) * 100

    result = {
        "state": state,
        "recommendation": recommendation,
        "electricity_price": electricity_price,
        "gas_price_per_m3": gas_price_per_m3,
        "electric_heat_cost_per_kwh": _round(electric_heat_cost),
        "effective_electric_heat_cost_per_kwh": _round(effective_electric_heat_cost),
        "gas_heat_cost_per_kwh": _round(gas_heat_cost),
        "cheapest_now": (
            "electricity" if effective_electric_heat_cost is not None and gas_heat_cost is not None and effective_electric_heat_cost <= gas_heat_cost
            else "gas" if effective_electric_heat_cost is not None and gas_heat_cost is not None
            else None
        ),
        "cost_difference_percent": round(savings, 1) if savings is not None else None,
        "cost_model": "purchased_energy_only",
        "solar_power_w": solar_power_w,
        "grid_power_w": grid_power_w,
        "solar_production": solar_production,
        "measured_solar_surplus_w": _round(measured_surplus),
        "surplus_source": surplus_source,
        "solar_surplus": solar_surplus,
        "partial_surplus": partial_surplus,
        "surplus_coverage_percent": round(surplus_coverage, 1) if surplus_coverage is not None else None,
        "required_surplus_w": required_surplus,
        "flexible_load_power_w": flexible_load_power_w,
        "solar_surplus_threshold_w": solar_surplus_threshold_w,
        "electric_efficiency": electric_efficiency,
        "gas_efficiency": gas_efficiency,
        "gas_kwh_per_m3": gas_kwh_per_m3,
        **timing,
    }
    if daily_context is not None and now is not None:
        result["daily_advisor"] = _daily_advice(
            future_prices or [], now, daily_context, flexible_load_power_w, language,
        )
    return result


def _future_context(
    current_price: float | None,
    prices: list[Any],
    now: datetime | None,
    electric_efficiency: float,
) -> dict[str, Any]:
    if current_price is None or not prices or now is None:
        return _empty_future()
    future = sorted((item for item in prices if item.time > now), key=lambda item: item.time)
    if not future:
        return _empty_future()
    threshold = current_price * 0.95 if current_price > 0 else current_price - 0.005
    better = next((item for item in future if item.price <= threshold), None)
    if better is None:
        return _empty_future()
    return {
        "next_better_time": better.time,
        "next_better_price": better.price,
        "minutes_until_better": max(0, round((better.time - now).total_seconds() / 60)),
        "future_electric_heat_cost": _round(better.price / electric_efficiency),
    }


def _empty_future() -> dict[str, Any]:
    return {
        "next_better_time": None,
        "next_better_price": None,
        "minutes_until_better": None,
        "future_electric_heat_cost": None,
    }


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _daily_advice(prices, now, context, power_w, language):
    """Add calendar-day planning to the existing advisor, using its shared optimizer.

    Grid-only windows and conditional solar alternatives remain separate models.
    A point-in-time measurement never becomes a claim of future measured surplus.
    """
    nl = language.lower().startswith("nl")
    now = now.astimezone(timezone.utc)
    forecast = context.get("forecast", {})
    minutes = context.get("resolution_minutes", 60)
    duration = context.get("duration_minutes", 60)
    if minutes not in (15, 60) or not isinstance(duration, (int, float)) or not isfinite(duration) or not 15 <= duration <= 1440:
        raise ValueError("Invalid Daily Advisor duration/resolution")
    duration = ceil(duration / minutes) * minutes
    power_w = power_w if power_w is not None and isfinite(power_w) and power_w > 0 else None
    step = timedelta(minutes=minutes)
    price_map = {item.time.astimezone(timezone.utc): item.price for item in prices if isfinite(item.price)}
    export_map = {item.time.astimezone(timezone.utc): item.price for item in context.get("export_prices", []) if isfinite(item.price)}
    solar_days = {day["date"]: day for day in forecast.get("days", [])}
    series = _power_series(forecast)
    # Forecast knots and interval boundaries recur across chart and solar integration.
    # Keep the memoization scoped to this calculation: no stale forecast values.
    power_at = cache(lambda stamp: _power_at(series, stamp))
    measurements = {item["role"]: item for item in forecast.get("measurements", []) if item.get("kind") == "measured"}
    grid = measurements.get("grid_power", {}).get("value")
    solar = measurements.get("solar_power", {}).get("value")
    baseline = None
    if grid is not None and solar is not None and solar >= 0 and solar + grid >= 0:
        grid_time = measurements["grid_power"].get("last_updated")
        solar_time = measurements["solar_power"].get("last_updated")
        if grid_time and solar_time:
            stamps = [datetime.fromisoformat(value).astimezone(timezone.utc) for value in (grid_time, solar_time)]
            # P1 and inverter sensors report at different rates. Both readings must
            # remain fresh; a stale measurement must not become a live baseline.
            if abs((stamps[0] - stamps[1]).total_seconds()) <= 300 and all(0 <= (now - stamp).total_seconds() <= 300 for stamp in stamps):
                baseline = solar + grid
    # Explain why the conditional solar scenario cannot be calculated.
    solar_diagnostics = []
    if power_w is None:
        solar_diagnostics.append("flexible_load_power_missing")
    if grid is None:
        solar_diagnostics.append("grid_power_unavailable")
    if solar is None:
        solar_diagnostics.append("solar_power_unavailable")
    if grid is not None and solar is not None and baseline is None:
        solar_diagnostics.append("power_measurements_not_synchronized")
    if not series:
        solar_diagnostics.append("solar_forecast_series_unavailable")
    days, advice, solar_options, chart = [], [], [], []
    today = now.astimezone(AMSTERDAM).date()
    for offset in range(3):
        day = today + timedelta(days=offset)
        start = datetime.combine(day, time.min, AMSTERDAM).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), time.min, AMSTERDAM).astimezone(timezone.utc)
        stamps = [start + step * i for i in range(int((end - start) / step))]
        known = [stamp for stamp in stamps if stamp in price_map]
        price_status = "known" if len(known) == len(stamps) else "partial" if known else "unknown"
        future = [stamp for stamp in stamps if stamp >= now]
        daily = {"date": day.isoformat(), "price_status": price_status, "known_intervals": len(known),
                 "expected_intervals": len(stamps), "solar_energy_kwh": solar_days.get(day.isoformat(), {}).get("energy_kwh"),
                 "solar_status": solar_days.get(day.isoformat(), {}).get("status", "missing"), "window": None}
        for stamp in stamps:
            solar_w = power_at(stamp.timestamp())
            chart.append({"time": stamp.isoformat(), "price": price_map.get(stamp), "solar_kw": solar_w / 1000 if solar_w is not None else None})
        # Never optimize across a missing price interval or extend the last known price.
        complete = future and all(stamp in price_map for stamp in future)
        if complete:
            entries = [PriceEntry(stamp, price_map[stamp]) for stamp in future]
            plan = optimize_intervals(entries, duration, alternatives=0, resolution_minutes=minutes)
            if plan:
                reference = sum(item.price for item in entries[:duration // minutes]) / (duration // minutes)
                daily["window"] = _daily_window(plan, power_w, reference, "earliest_remaining_grid_window", "grid_only", nl)
                advice.append(daily["window"])
            if power_w is not None and baseline is not None and series:
                adjusted = []
                missing_export_price = False
                incomplete_solar_interval = False
                solar_times = set()
                for item in entries:
                    own = _solar_load_energy(series, item.time.timestamp(), (item.time + step).timestamp(), baseline, power_w, power_at=power_at)
                    if own is None:
                        incomplete_solar_interval = True
                        continue
                    if item.time not in export_map:
                        missing_export_price = True
                        continue
                    if own > 0:
                        solar_times.add(item.time)
                    load = power_w / 1000 * minutes / 60
                    cost = (load - own) * item.price + own * export_map[item.time]
                    adjusted.append(PriceEntry(item.time, cost / load))
                if incomplete_solar_interval:
                    solar_diagnostics.append("solar_forecast_interval_incomplete")
                if missing_export_price:
                    solar_diagnostics.append("export_price_missing")
                solar_plan = optimize_intervals(
                    adjusted, duration, alternatives=0, resolution_minutes=minutes,
                    window_filter=lambda window, times=solar_times: any(item.time in times for item in window),
                )
                if not solar_plan:
                    solar_diagnostics.append("no_eligible_solar_window")
                if solar_plan:
                    selected = [datetime.fromisoformat(item["time"]) for item in solar_plan["selected_intervals"]]
                    reference = sum(price_map[stamp] for stamp in selected) / len(selected)
                    option = _daily_window(solar_plan, power_w, reference, "same_window_grid_only", "solar_scenario", nl)
                    option["assumed_household_load_w"] = round(baseline, 1)
                    solar_options.append(option)
        days.append(daily)
    if solar_options:
        advice.append(min(solar_options, key=lambda item: item["estimated_cost_eur"]))
    elif not solar_diagnostics:
        solar_diagnostics.append("no_eligible_solar_window")
    grid_windows = [day["window"] for day in days if day["window"]]
    best = min(grid_windows, key=lambda item: item["average_price_eur_kwh"], default=None)
    return {
        "state": "ready" if best else "unavailable", "best_window": best, "days": days,
        "advice": advice[:3], "chart": chart[:300], "forecast_quality": forecast.get("quality", "unavailable"),
        "measured_grid_power_w": grid, "measured_export_w": max(0, -grid) if grid is not None else None,
        "assumed_household_load_w": baseline, "realized_savings_eur": None,
        "solar_scenario_available": bool(solar_options),
        "solar_diagnostics": sorted(set(solar_diagnostics)) if not solar_options else [],
        "heat_cost_note": ("Het bestaande warmteadvies vergelijkt ingekochte energie; gemiste terugleveropbrengst is daarin niet meegerekend."
                           if nl else "Existing heat advice compares purchased energy; foregone export revenue is not included in that heat model."),
        "summary": (("Plan alleen binnen gepubliceerde prijsintervallen. Zonneadvies is een voorwaardelijk scenario."
                     if best else "Nog geen volledig geprijsd gebruiksvenster beschikbaar.") if nl else
                    ("Plan only within published price intervals. Solar advice is a conditional scenario."
                     if best else "No fully priced consumption window is available yet.")),
    }


def _daily_window(plan, power_w, reference, reference_kind, model, nl):
    energy = power_w / 1000 * plan["duration_minutes"] / 60 if power_w is not None else None
    cost = energy * plan["average_price"] if energy is not None else None
    delta = energy * reference - cost if energy is not None else None
    scenario = model == "solar_scenario"
    reason = (("Zonneoptie bij gelijkblijvend huishoudelijk verbruik; gemiste terugleveropbrengst en terugleverkosten zijn meegerekend."
               if scenario else "Laagste gemiddelde all-in prijs binnen de resterende volledig geprijsde dag; kosten veronderstellen netstroom.") if nl else
              ("Solar option assuming unchanged household demand; foregone export revenue and export fees are included."
               if scenario else "Lowest average all-in price within the remaining fully priced day; costs assume grid supply."))
    return {"start": plan["recommended_start"].isoformat(), "end": plan["recommended_end"].isoformat(),
            "duration_minutes": plan["duration_minutes"], "average_price_eur_kwh": plan["average_price"],
            "estimated_cost_eur": round(cost, 4) if cost is not None else None,
            "estimated_impact_eur": round(delta, 4) if delta is not None else None,
            "reference": reference_kind, "model": model, "confidence": "low" if scenario else "medium",
            "reason": reason, "impact_kind": "hypothetical_not_realized", "energy_kwh": energy,
            "data_quality": {"prices": "published", "solar": "conditional_forecast" if scenario else "not_used",
                             "load": "configured_constant" if power_w is not None else "unknown"}}


def _power_series(forecast):
    """Use complete, validated PR1 installation coverage only."""
    groups = forecast.get("series", [])
    if forecast.get("series_status") != "available" or not groups or len(groups) != forecast.get("installation_count"):
        return []
    if len({group["installation"] for group in groups}) != len(groups):
        return []
    result = []
    for group in groups:
        points = [(datetime.fromisoformat(point["time"]).timestamp(), point["power_w"])
                  for point in group["points"] if point.get("power_w") is not None]
        if not points:
            return []
        result.append(sorted(points))
    return result


def _power_at(series, stamp):
    """Linear interpolation only between adjacent forecast points at most an hour apart."""
    if not series:
        return None
    total = 0.0
    for points in series:
        index = bisect_right(points, (stamp, float("inf"))) - 1
        if index < 0:
            return None
        left, watts = points[index]
        if left == stamp:
            total += watts
        elif index + 1 < len(points) and 0 < points[index + 1][0] - left <= 3600:
            right, next_watts = points[index + 1]
            total += watts + (next_watts - watts) * (stamp - left) / (right - left)
        else:
            return None
    return total


def _solar_load_energy(series, start, end, baseline, power, *, power_at=None):
    """Integrate bounded, piecewise-linear surplus; never bridge overnight gaps."""
    if power_at is None:
        power_at = lambda stamp: _power_at(series, stamp)
    knots = {start, end}
    for points in series:
        first = bisect_right(points, (start, float("inf")))
        last = bisect_right(points, (end, float("-inf")))
        knots.update(stamp for stamp, _ in points[first:last])
    knots = sorted(knots)
    energy = 0.0
    for left, right in zip(knots, knots[1:], strict=False):
        values = [power_at(stamp) for stamp in (left, (left + right) / 2, right)]
        if any(value is None for value in values):
            return None
        first, _, last = values
        cuts = [0.0, 1.0]
        if first != last:
            cuts.extend((limit - first) / (last - first) for limit in (baseline, baseline + power) if 0 < (limit - first) / (last - first) < 1)
        cuts.sort()
        for a, b in zip(cuts, cuts[1:], strict=False):
            clipped = [min(power, max(0, first + (last - first) * fraction - baseline)) for fraction in (a, b)]
            energy += sum(clipped) / 2 * (b - a) * (right - left) / 3600000
    return energy
