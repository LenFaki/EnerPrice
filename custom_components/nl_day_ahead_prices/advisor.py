"""Central EnerPrice advice engine."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def build_price_advice(
    *,
    current_price: float | None,
    all_in_price: float | None,
    score: dict[str, Any],
    rating: str | None,
    trend: str,
    volatility: str | None,
    language: str = "en",
    prices: list[Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Combine price signals into one practical recommendation."""
    numeric = score.get("score")
    if numeric is None:
        return _advice("neutral", 50, current_price, all_in_price, rating, trend, volatility, language, prices, now)
    if numeric >= 90 or (all_in_price is not None and all_in_price < 0):
        state = "excellent"
    elif numeric >= 70:
        state = "good"
    elif numeric < 10 or (numeric < 20 and trend in {"rising", "strongly_rising"}):
        state = "critical"
    elif numeric < 40:
        state = "avoid"
    else:
        state = "neutral"
    return _advice(state, numeric, current_price, all_in_price, rating, trend, volatility, language, prices, now)


def _advice(
    state: str,
    score: float,
    current_price: float | None,
    all_in_price: float | None,
    rating: str | None,
    trend: str,
    volatility: str | None,
    language: str,
    prices: list[Any] | None,
    now: datetime | None,
) -> dict[str, Any]:
    is_dutch = language.lower().startswith("nl")
    content = (
        {
            "excellent": ("Zeer goedkoop moment", "Dit is een van de goedkoopste beschikbare periodes.", "Goed moment om grootverbruikers te starten."),
            "good": ("Gunstige prijs", "De huidige prijs is lager dan de meeste beschikbare periodes.", "Gebruik flexibele apparaten waar mogelijk nu."),
            "neutral": ("Normale prijs", "De huidige prijs ligt rond het normale niveau.", "Er is geen directe actie nodig."),
            "avoid": ("Duur moment", "Er zijn goedkopere periodes beschikbaar.", "Stel grootverbruikers indien mogelijk uit."),
            "critical": ("Zeer duur moment", "Dit is een van de duurste beschikbare periodes.", "Vermijd nu onnodig energieverbruik."),
        }
        if is_dutch
        else {
            "excellent": ("Very cheap moment", "This is one of the cheapest available periods.", "Good time to start large consumers."),
            "good": ("Favorable price", "The current price is below most available periods.", "Use flexible appliances now where practical."),
            "neutral": ("Normal price", "The current price is around the normal range.", "No urgent action is needed."),
            "avoid": ("Expensive moment", "Cheaper periods are available.", "Postpone large consumers if possible."),
            "critical": ("Very expensive moment", "This is one of the most expensive available periods.", "Avoid optional consumption now."),
        }
    )
    title, message, recommendation = content[state]
    cheap_actions = (
        ["EV laden", "Boiler verwarmen", "Wasmachine draaien", "Thuisbatterij laden"]
        if is_dutch
        else ["Charge EV", "Heat boiler", "Run washing machine", "Charge home battery"]
    )
    expensive_actions = (
        ["EV laden", "Droger gebruiken", "Boiler elektrisch bijverwarmen"]
        if is_dutch
        else ["Charge EV", "Use dryer", "Use electric boiler boost"]
    )
    timing = _upcoming_price_context(all_in_price, prices or [], now)
    summary = _summary(state, timing, language)
    return {
        "state": state,
        "title": title,
        "message": message,
        "recommendation": recommendation,
        "summary": summary,
        "score": round(score),
        "current_price": current_price,
        "all_in_price": all_in_price,
        "rating_5_level": rating,
        "trend": trend,
        "volatility": volatility,
        "best_actions": cheap_actions if state in {"excellent", "good"} else [],
        "avoid_actions": expensive_actions if state in {"avoid", "critical"} else [],
        "reason": (
            f"Score {round(score)}/100, beoordeling {rating or 'onbekend'}, trend {trend}."
            if is_dutch
            else f"Score {round(score)}/100, rating {rating or 'unknown'}, trend {trend}."
        ),
        **timing,
    }


def _upcoming_price_context(
    current_price: float | None,
    prices: list[Any],
    now: datetime | None,
) -> dict[str, Any]:
    """Describe the next materially cheaper interval and the best upcoming price."""
    if current_price is None or not prices:
        return {
            "next_better_time": None,
            "next_better_price": None,
            "minutes_until_better": None,
            "savings_percent": None,
            "best_upcoming_time": None,
            "best_upcoming_price": None,
        }
    now = now or datetime.now(timezone.utc)
    future = sorted(
        (item for item in prices if item.time > now),
        key=lambda item: item.time,
    )
    if not future:
        return {
            "next_better_time": None,
            "next_better_price": None,
            "minutes_until_better": None,
            "savings_percent": None,
            "best_upcoming_time": None,
            "best_upcoming_price": None,
        }
    best = min(future, key=lambda item: item.price)
    threshold = current_price * 0.95 if current_price > 0 else current_price - 0.005
    better = next((item for item in future if item.price <= threshold), None)
    savings = None
    if better is not None and current_price > 0:
        savings = round(max(0.0, (current_price - better.price) / current_price * 100), 1)
    return {
        "next_better_time": better.time if better else None,
        "next_better_price": better.price if better else None,
        "minutes_until_better": (
            max(0, round((better.time - now).total_seconds() / 60))
            if better
            else None
        ),
        "savings_percent": savings,
        "best_upcoming_time": best.time,
        "best_upcoming_price": best.price,
    }


def _summary(state: str, timing: dict[str, Any], language: str) -> str:
    """Return a short dashboard-friendly recommendation."""
    is_dutch = language.lower().startswith("nl")
    minutes = timing.get("minutes_until_better")
    savings = timing.get("savings_percent")
    if minutes is not None and state in {"neutral", "avoid", "critical"}:
        if is_dutch:
            detail = f" over ongeveer {minutes} min"
            if savings is not None:
                detail += f" ({savings:.0f}% goedkoper)"
            return f"Wacht indien mogelijk; een gunstiger prijsinterval begint{detail}."
        detail = f" in about {minutes} min"
        if savings is not None:
            detail += f" ({savings:.0f}% cheaper)"
        return f"Wait if practical; a better-priced interval starts{detail}."
    if state in {"excellent", "good"}:
        return (
            "Goed moment voor flexibel energieverbruik."
            if is_dutch
            else "Good time for flexible energy use."
        )
    return (
        "Geen sterke reden om flexibel verbruik nu te verschuiven."
        if is_dutch
        else "No strong reason to shift flexible energy use right now."
    )
