"""A Belgian wholesale dashboard using only resolved, live entity IDs."""

import json


def generate_market_dashboard_yaml(entity_ids, language="en", **_options):
    """JSON is valid YAML; do not guess missing IDs or require custom cards."""
    nl = language == "nl"
    cards = [{"type": "markdown", "content": (
        "## Belgische marktprijzen\nEUR/kWh, exclusief btw, belastingen, nettarieven en leverancierskosten. "
        "Planning vergelijkt alleen marktprijzen; dit is geen contractprijs of factuur."
        if nl else
        "## Belgian wholesale prices\nEUR/kWh, excluding VAT, taxes, grid tariffs and supplier fees. "
        "Planning compares market prices only; these are not contract prices or bills."
    )}]
    keys = ("current_market_price", "tomorrow_prices_available", "best_price_period",
            "next_best_price_period_start", "current_provider")
    entities = [entity_ids[key] for key in keys if key in entity_ids]
    if entities:
        cards.append({"type": "entities", "entities": entities})
    price_entity = entity_ids.get("current_market_price")
    if price_entity:
        # HA's built-in history graph needs no ApexCharts installation.
        cards.append({"type": "history-graph", "hours_to_show": 24, "entities": [price_entity]})
        cards.append({"type": "markdown", "content": (
            "| Tijd / Time | EUR/kWh (ex. VAT) |\n|---|---:|\n"
            + "{% for p in state_attr(" + repr(price_entity) + ", 'prices') or [] %}\n"
            + "| {{ as_timestamp(p.time) | timestamp_custom('%d-%m %H:%M %Z') }} | {{ p.price | round(5) }} |\n{% endfor %}"
        )})
    return json.dumps({"title": "EnerPrice BE", "views": [{"title": "Marktprijzen" if nl else "Market prices",
                       "path": "market", "cards": cards}]}, ensure_ascii=False, indent=2)
