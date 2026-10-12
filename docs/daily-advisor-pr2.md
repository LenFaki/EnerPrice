# Daily Energy Advisor PR2

PR2 extends the **existing** `build_smart_energy_advice` entry point. It uses the
shared `optimize_intervals` optimizer, `all_in_entries_for_supplier` and existing
supplier export-fee calculations. It is not a separate competing pricing engine.
PR1 forecast inputs and `get_daily_forecast` remain unchanged.

Manifest/project version stays **2.5.4**. No configuration migration, automatic
device control, notifications, persistent savings data, merge or release.

## Configuration

Enable the Daily Advisor preview in EnerPrice options. Existing installations
remain disabled by default. Keep the PR1 Forecast.Solar today/tomorrow selections.

- **Flexible appliance power:** use the existing Smart Setup power field, in W.
  Stored power is reused even if the legacy Smart Advisor is disabled. Unknown
  power still permits a price window, but costs/financial impact remain null.
- **Daily duration:** new optional setting, default 60 minutes, 15-1440 minutes
  in 15-minute increments. Hourly pricing rounds up to complete hours; returned
  `duration_minutes` and energy/cost use that actual rounded duration.
- **Actual solar power:** new optional single power sensor (W or kW), alongside
  PR1's grid power input. Do not substitute an energy counter for power.
- Both measurement timestamps must be at most 15 minutes old and within 60 seconds
  of each other for a solar financial scenario. Otherwise no household-load
  assumption is made. Missing optional inputs never become zero.

Disabling either setup preserves saved values. Source changes rebuild listeners;
duration/power changes update advice without forcing a market API request.

## Two explicit models

### Grid-only reference

For each Amsterdam calendar date, the advisor uses complete, published intervals
remaining after now and calls the existing optimizer for a consecutive window.
Partly elapsed intervals are skipped. A gap in the remaining prices prevents a
cheapest-window claim for that date; prices are never extended across a gap.
Windows stay within a calendar day rather than crossing midnight.

The reference is the **earliest remaining feasible window on that same day**,
also entirely on grid supply. Estimated energy = configured power x rounded
duration. Estimated cost = energy x average all-in price. Financial impact =
reference cost minus selected cost, which can be zero. Negative tariffs and
negative costs are retained, not clipped.

The `Best Consumption Window` sensor selects the cheapest **grid-only** daily
window among the available dates. This explicit baseline avoids silently ranking
uncertain solar scenarios as guaranteed cheaper than price-only windows.

### Conditional solar alternative

The advisor may add one solar alternative, labelled **low confidence**:

1. Infer current household load as actual solar production + signed grid power
   (positive import, negative export). Reject inconsistent negative load.
2. Assume that household load remains constant for the proposed window. This is
   a scenario, not a consumption forecast or a claim of future measured surplus.
3. Interpolate validated instantaneous forecast power only between adjacent
   points no more than one hour apart, for every configured installation.
   Do not bridge overnight gaps or infer energy from sparse Wh-period data.
4. Integrate production above assumed household load, capped at appliance power.
   Subtract household demand **before** assigning production to the appliance.
   Mixed solar/grid windows are supported; a solar alternative must contain some
   positive solar contribution.
5. Price remaining grid energy at the existing all-in import tariff. Price solar
   used by the appliance at the **foregone net export revenue**, including the
   applicable supplier's export/feed-in/imbalance fees and VAT semantics for each
   interval's local date. Custom supplier overrides remain constant.

For this model, estimated cost is economic incremental cost (grid purchase plus
foregone export revenue), not merely an invoice estimate. Financial impact compares
against **the same window entirely on grid supply**, a hypothetical counterfactual.
It is not a demonstrated saving. Negative impact means this scenario costs more
than that reference. High export revenue can make self-consumption less attractive;
negative export remuneration can make avoided export valuable.

The grid-only best window and solar alternative are deliberately separate. When
load/forecast/export prices are insufficient, only grid-only advice is returned.
No production-only shortcut treats all predicted solar as free available surplus.
Measured energy counters are not transformed into guessed baseline consumption.

The existing electric-versus-gas advice remains unchanged. Its purchased-energy
heat model does not account for foregone export revenue; Daily Advisor output and
dashboard state that limitation explicitly.

## Output and entities

When Daily Advisor is enabled, two additional stable unique IDs attach to the
existing EnerPrice device:

- `daily_energy_advice`: state `ready` or `unavailable`, up to three advice records,
  three-day outlook, data quality, measurement context and a bounded chart.
- `best_consumption_window`: timestamp of the best grid-only window, with its
  model, end, duration, costs, reference, impact and confidence as attributes.

Existing entities and their unique IDs are not repurposed. Forecast and market
coordinator updates both refresh the advice entities; the existing Smart Energy
Advisor also subscribes to forecast updates. Responses remain useful when market
prices are unavailable: three dates are returned but no fabricated windows/costs.

```yaml
action: nl_day_ahead_prices.get_smart_energy_advice
data:
  language: nl
response_variable: advice
```

New results live under `daily_advisor`, alongside the original Smart Energy
response fields. Explicit service appliance-power/language overrides are respected.
Daily duration comes from options. Existing service behavior is retained when
Daily Advisor is disabled.

Each advice record includes `start`, `end`, `duration_minutes`, `model`, `reason`,
`confidence`, `data_quality`, `energy_kwh`, `estimated_cost_eur`,
`estimated_impact_eur`, `reference` and `impact_kind: hypothetical_not_realized`.
`realized_savings_eur` is always null. Do not turn these estimates into recorded
realized savings without PR3's defensible baseline and measurement checks.

The outlook covers today and the next two **calendar dates in Europe/Amsterdam**.
`price_status` is `known`, `partial` or `unknown`; solar status is separate.
23/24/25-hour dates and repeated autumn quarter-hours are preserved through UTC
interval arithmetic. Unknown third-day prices remain null even if solar points
exist. PR1's unavailable third-day solar total remains unknown.

Chart data is capped at 300 interval rows and marked unrecorded on the new advice
sensor. The nested daily payload is also unrecorded on the legacy Smart Advisor.
It contains `{time, price, solar_kw}`; null gaps are never replaced by zero.
Shared calculations use a single bounded cache entry, invalidated by price,
forecast, settings, language and minute changes. No persistent history is added.

## Optional dashboard

```yaml
action: nl_day_ahead_prices.generate_dashboard_yaml
data:
  dashboard_type: energy_advisor
  language: nl
  include_daily_advisor: true
  use_apexcharts: false
response_variable: dashboard
```

The new section shows forecast totals, the grid-only best window, quality, up to
three reasoned advice records and the three-day availability table. Set
`use_apexcharts: true` for a combined price/solar chart. False removes all custom
chart cards; the built-in tiles and Markdown tables remain usable.

Defaults preserve the previous layouts: `include_daily_advisor: false`,
`use_apexcharts: true`. Actual current entity IDs are resolved through the existing
entity registry, including renamed IDs. Missing Daily Advisor entities produce
an explanatory card rather than invented entity references. No dashboard is
installed automatically.

## Nederlands

Schakel Daily Advisor in bij de opties. Stel het bestaande apparaatvermogen in
bij Smart Setup en kies de gebruiksduur bij Daily Advisor. Optioneel: selecteer
werkelijk zonnevermogen naast het netvermogen voor een voorwaardelijk zonnescenario.

**Beste gebruiksvenster rekent met netstroom.** Een zonneoptie blijft een apart,
onzeker scenario met gelijkblijvend huishoudelijk verbruik. Zonneproductie is geen
overschot; gemiste terugleveropbrengst wordt meegerekend. Het getoonde financiele
verschil is hypothetisch, niet een gerealiseerde besparing. Ontbrekende prijzen
blijven onbekend. Meldingen en besparingscontrole volgen pas in PR3.

## Upgrade, rollback and validation

No version bump or migration. Existing enabled PR1 users receive two additional
read-only entities. Existing dashboards do not change until regenerated with the
new flag. Disabling the preview stops its inputs and preserves all options.
Rollback to the previous main/PR1 build and restart HA; it ignores new option keys.
Remove optional stale entity-registry entries manually if desired. Do not rewrite
configuration storage.

Tests cover signed prices/export values, mixed solar/grid windows, explicit cost
references, partial coverage, unknown future prices, separate roofs, quarter hours,
DST, cached result invalidation, service overrides, listener cleanup, translated
advice, actual entity-ID resolution and parsed YAML/rendered Jinja. Default
dashboard output is checked against PR1 hashes in all three layouts and NL/EN.
HA boundaries use doubles; live-installation validation is still recommended.
