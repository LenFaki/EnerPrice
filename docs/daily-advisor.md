# Daily Energy Advisor: PR1 development preview

This is the forecast/configuration foundation for v2.6, not the complete advisor.
Manifest and project versions remain 2.5.4. Existing installations are unchanged
until the user explicitly enables **Daily Advisor forecasts (preview)** in options.

## Delivery sequence

1. **This PR:** optional configuration, validated local solar inputs, daily totals,
   data-quality sensors, bounded response service, tests and translations.
2. **Next PR:** extend the existing Smart Energy Advisor and price helpers with
   solar-aware windows, confidence/reasons/financial impact, three-day context and
   optional dashboard cards. Preserve all-in/export fees, negative prices, quarter
   hours and existing electric/gas calculations; expose opportunity-cost limits.
3. **Following PR:** opt-in notifications with explicit consent and persistent
   deduplication, plus bounded forecast/measurement storage and savings comparisons.

Future defaults: Forecast.Solar, three calendar dates including today,
notifications preferred but **never sent without explicit confirmation and a valid
target**, savings checks preferred. Email and automatic device control remain off
and are not implemented. Notification/savings controls are deliberately absent
from this first PR rather than displaying settings that do nothing.

## Configuration / configuratie

Open EnerPrice options and enable the Daily Advisor preview. After supplier and
Smart Setup options, select Forecast.Solar daily totals. Smart Setup can remain
disabled; neither feature erases the other's stored selections.

- Select **total estimated energy today** and **total estimated energy tomorrow**
  for each installation. Remaining-today and hourly sensors are rejected.
- Select each physical installation once. Up to eight installations are supported.
  Multiple planes already combined within one Forecast.Solar entry must not also
  be added as separate duplicate installations. Registry entry/day deduplication
  cannot detect two independently configured entries for the same physical roof.
- Optional grid power: at most one power-class sensor in W or kW; positive means
  import and negative means export.
- Optional actual solar energy: at most one energy-class sensor in Wh or kWh.
- Optional consumption energy: up to eight energy-class sensors in Wh or kWh.
  These are individual meter readings, **not summed or treated as daily consumption**.
  Total/lifetime counters need future baseline/delta handling before comparison.
- Empty selections are valid. Clear a list to remove an input. Disabling the
  preview preserves selections, even while their source sensors are offline.

**Nederlands:** schakel in de opties de Daily Advisor-preview in. Kies per
Forecast.Solar-installatie uitsluitend de dagtotalen van vandaag en morgen.
Kies niet de resterende opwek of uurwaarden. Netvermogen en werkelijke energie
zijn optioneel. Lege selecties mogen; uitschakelen bewaart bestaande instellingen.
Dit onderdeel verstuurt geen meldingen en bedient geen apparaten.

## Verified Forecast.Solar contract

Checked against Home Assistant **2026.10.1**:

- [Sensors](https://github.com/home-assistant/core/blob/2026.10.1/homeassistant/components/forecast_solar/sensor.py):
  daily total sensor identity is `{config_entry_id}_energy_production_today` or
  `_energy_production_tomorrow`, with energy device class. Native Wh may be displayed
  as kWh. EnerPrice validates actual units rather than assuming the display unit.
  Full forecast arrays are not sensor attributes.
- [Cached forecast service](https://github.com/home-assistant/core/blob/2026.10.1/homeassistant/components/forecast_solar/services.py):
  `forecast_solar.get_forecast`, `config_entry`, `resolution: raw`, returns `watts`
  and `wh_period` timestamp maps from the integration's existing coordinator data.
  EnerPrice does not call a second external API or access private coordinator data.
- [Update cadence](https://github.com/home-assistant/core/blob/2026.10.1/homeassistant/components/forecast_solar/coordinator.py):
  hourly for free accounts, 30 minutes with an API key. EnerPrice reads the local
  response at most on its 30-minute coordinator schedule. State events and quarter
  boundaries update local summaries without refetching market prices.

Older HA versions without the response service still provide validated sensor
totals. Unsupported response shapes fail closed; no invented attribute parser.
Each selected Forecast.Solar config entry is read once per refresh, not once per
selected today/tomorrow sensor. Calls are bounded by a five-second timeout each.

## Data quality and semantics

- Forecast states older than two hours, future-dated, restored or invalid are
  unknown. Relative today/tomorrow states must have been updated on the current
  Amsterdam date. Yesterday's value is never relabeled as today's value.
- Freshness uses **entity last_updated**, not proof of an upstream successful
  fetch. An unchanged entity may conservatively become stale. This favors missing
  advice over falsely fresh advice. Measurement age limit is 15 minutes.
- A missing configured installation makes the combined total unavailable; partial
  sums are not presented as complete roof totals.
- Today, tomorrow and the day after are calendar dates in Europe/Amsterdam, not
  consecutive 24-hour blocks. UTC-normalized series preserve repeated autumn
  hours and quarter-hour timestamps.
- Native entities only establish daily totals for today/tomorrow. Third-day total
  remains null/missing. Third-day series points are returned if the local response
  supplies them. Sparse series are not extrapolated into a full daily total.
- No electricity prices are predicted here. No cheapest window, financial impact
  or savings claim is produced without the future advisor work.
- Forecast production is **not available surplus**. `solar_surplus_kwh` is null.
  Measured meter readings and forecast records are explicitly distinguished.

The diagnostic state is `partial` when at least one daily total is available,
otherwise `unavailable`. It remains partial even with today and tomorrow present,
because the third daily total is unknown. Series availability is reported separately.

## Entities and response data

Three sensors appear only when enabled: Solar Forecast Today (kWh), Solar Forecast
Tomorrow (kWh), and Forecast Data Quality (diagnostic). They use new stable unique
IDs based on the EnerPrice config entry and attach to the existing EnerPrice device.
Forecasts deliberately have no energy-total state class: they are not measurements.

```yaml
action: nl_day_ahead_prices.get_daily_forecast
data: {}
response_variable: daily_forecast
```

The response contains `enabled`, `days` (exactly three when enabled), `issues`,
`installation_count`, `quality`, `freshness_basis`, `measurements`, `series_status`
and `series`. Series are grouped by installation; points contain `time`, `power_w`
and `energy_kwh`. Wh-period values are retained, but interval durations are not
inferred from gaps between sunlight timestamps. Do not assume consecutive points
define uniform or continuous intervals.

The service reads local memory/states only. Disabled mode returns `enabled: false`
and empty arrays. No persistent forecast files are written in PR1. Limits: eight
installations, 1,200 source points per installation/map, three calendar dates.
Only compact three-day summaries enter sensor attributes, never full series.

## Optional manual dashboard example

Use the actual new entity IDs from your HA installation; renamed entities work
because forecast inputs resolve through the entity registry. This built-in card
requires no ApexCharts. Automatic generator integration belongs to PR2.

```yaml
type: entities
title: Zonneprognose
entities:
  - entity: sensor.REPLACE_WITH_SOLAR_FORECAST_TODAY
  - entity: sensor.REPLACE_WITH_SOLAR_FORECAST_TOMORROW
  - entity: sensor.REPLACE_WITH_FORECAST_DATA_QUALITY
```

## Upgrade, rollback and verification

Test on a backed-up HA installation; this is an unreleased feature branch. Existing
configuration needs no migration. Enabling changes only EnerPrice options and adds
three optional sensors. Disable the preview to stop listeners/local service reads;
stored selections remain. To roll back, reinstall v2.5.4 and restart HA. Old code
ignores the new option keys; optional entity-registry entries may remain unavailable
and can be removed manually. Existing entity IDs, prices, tariffs and services are
not replaced. Never overwrite config-entry storage manually.

Automated tests cover parsing, unit conversion, missing/stale sources, registry
identity, duplicate installations, 23/24/25-hour dates, quarter hours, response
bounds, local-only service reads, options preservation and listener cleanup.
The existing price/advisor/dashboard regression suite remains required. HA runtime
boundaries use doubles; a live installation check remains necessary before release.
