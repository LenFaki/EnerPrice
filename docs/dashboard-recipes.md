# EnerPrice dashboard recipes

These recipes are copy-ready starting points for Home Assistant. Replace example entity IDs with the IDs from your installation. EnerPrice remains advisory: examples do not directly control a device unless you deliberately build an automation around the returned advice.

## 1. Current all-in price

Use a native Tile card:

```yaml
type: tile
entity: sensor.nl_day_ahead_prices_current_all_in_price
name: Electricity now
```

If your entity ID differs, select **Current All-in Price** from the EnerPrice device page.

## 2. Price Advisor

```yaml
type: tile
entity: sensor.nl_day_ahead_prices_price_advisor
name: Price advice
```

The Price Advisor is the quick price-only recommendation. It does not require gas, solar or P1 entities.

## 3. Smart Setup and smart hot-water advice

Starting with v2.5, **Smart Setup** can optionally be enabled in the EnerPrice options. It stores the grid/P1 entity, actual solar-production entity, gas-price entity, one flexible-load name and power, efficiencies, gas energy content, surplus threshold and advice language. Smart Energy Advisor then uses these values automatically instead of requiring the same entity IDs and settings on every call.

Smart Setup is optional and remains disabled by default for existing installations. Existing v2.4.x calls keep working. Values explicitly supplied in a service call always take precedence over stored Smart Setup values.

The Smart Energy Advisor is available both as a response action and, when Smart Setup is enabled, as a persistent sensor. A practical setup uses:
- an EnerPrice config entry with current all-in prices;
- a gas-price entity in EUR/m³ (optional);
- a grid/P1 power entity in W (recommended);
- optionally the electrical demand of the flexible load in W.

Grid power must use **positive = import** and **negative = export**. Unknown, unavailable and missing values are not treated as zero.

When grid/P1 power is available, measured export is the surplus source. If grid power is missing but actual solar production is available, EnerPrice uses production only as an explicit fallback (`surplus_source: solar_production_fallback`). This is **not measured net surplus**, because household consumption is unknown. Without either source, `surplus_source` remains `unknown`.

Example action:

```yaml
action: nl_day_ahead_prices.get_smart_energy_advice
data:
  gas_price_entity: sensor.your_gas_price
  grid_power_entity: sensor.your_grid_power
  flexible_load_power_w: 1500
  electric_efficiency: 1.0
  gas_efficiency: 0.90
  gas_kwh_per_m3: 9.769
  solar_surplus_threshold_w: 500
  language: en
```

With `flexible_load_power_w`, EnerPrice distinguishes full surplus from partial surplus. For a 1500 W load, 500 W export is 33.3% coverage rather than enough surplus to run fully on exported energy. The remaining grid share is included in the effective electric heat cost.

Useful response fields include `state`, `recommendation`, `cheapest_now`, `electric_heat_cost_per_kwh`, `effective_electric_heat_cost_per_kwh`, `gas_heat_cost_per_kwh`, `measured_solar_surplus_w`, `surplus_source`, `partial_surplus`, `surplus_coverage_percent`, `required_surplus_w`, `cost_model`, `next_better_time` and `minutes_until_better`.

In v2.5, `effective_electric_heat_cost_per_kwh` uses the `purchased_energy_only` model: available surplus is assigned no additional purchase cost and only the remaining grid share is priced at the current all-in tariff. This is **not a complete economic cost model**; possible lost feed-in value/opportunity cost of self-consumed energy is not included.

## 4. Generate an EnerPrice dashboard

Instead of writing a dashboard manually, run:

```yaml
action: nl_day_ahead_prices.generate_dashboard_yaml
data:
  dashboard_type: energy_advisor
  include_market_price: true
  include_all_in_price: true
  include_supplier_info: true
  include_best_periods: true
  include_price_advisor: true
```

Copy the returned YAML into a Home Assistant dashboard. `compact` and `full` are also available.

## 5. Safe automation pattern

Treat Smart Energy Advisor output as advice and add your own safety conditions before switching a real load. For hot water, preserve the appliance's own thermostat, legionella/hygiene program, maximum temperature, minimum run time and manufacturer controls. Never interpret an unavailable sensor as permission to switch on.

## Quick setup checklist

1. Install and configure EnerPrice.
2. Verify **Current All-in Price** has a plausible value.
3. Open the EnerPrice options and optionally enable **Smart Setup**.
4. Select the grid/P1 entity and verify its sign convention.
5. Add a gas-price entity if heat-source comparison is wanted.
6. Select actual solar production when additional context/fallback is wanted.
7. Enter the flexible load's real electrical demand when known.
8. Test Smart Energy Advisor manually in Developer Tools > Actions; with Smart Setup, stored defaults no longer need to be repeated in `data:`.
9. Generate/copy a dashboard recipe.
10. Only then build device-control automations with explicit safety conditions.


## Phase 4: adaptive Smart Energy dashboard

The dashboard generator can resolve the actual EnerPrice entity IDs for a selected config entry. When Smart Setup is enabled it adds only the configured context entities (grid/P1 power, solar production and gas price) and the configured flexible-load name/power. Missing optional inputs are omitted instead of producing broken cards.

When multiple EnerPrice config entries are loaded, pass `config_entry_id`. `language: auto` follows the Home Assistant language; `en` and `nl` can be selected explicitly. Set `include_smart_energy: false` to omit the Smart Energy context.

Dashboard generation remains advisory only and does not control devices.


## V2.5 practical validation

After enabling Smart Setup, verify the persistent **Smart Energy Advisor** entity on the EnerPrice device page. Change or observe one configured input (grid/P1, solar or gas) and confirm that the advisor state/attributes refresh. Generate an `energy_advisor` dashboard and confirm it references the actual advisor entity ID, shows only configured optional inputs, and remains usable on a narrow mobile view.

Expected states are `solar_surplus`, `cheap_grid`, `wait`, `gas` and `normal`. Check both hourly and quarter-hour configurations when available. Missing/unavailable inputs must remain unknown; they must never become a false zero-value signal. Explicit values passed to `get_smart_energy_advice` continue to override stored Smart Setup defaults.

V2.5 intentionally supports one flexible load and advice only. Multiple loads, EV-specific Smart Setup logic, battery optimization and full export/opportunity-cost optimization remain outside this release scope.
