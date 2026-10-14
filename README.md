<p align="center">
  <img src="https://raw.githubusercontent.com/LenFaki/EnerPrice/main/brand/enerprice-header-en.png" alt="EnerPrice - Dynamic Energy Prices for Home Assistant" width="100%">
</p>

# EnerPrice

**Language:** English | [Nederlands](README.nl.md)

**EnerPrice 2.6:** Daily Energy Advisor supports optional solar-aware scenarios,
consent-gated morning briefings and conservative forecast-versus-actual checks.
Potential savings are not verified realized savings.

[![GitHub Release](https://img.shields.io/github/v/release/LenFaki/EnerPrice?style=flat-square)](https://github.com/LenFaki/EnerPrice/releases)
[![Status](https://img.shields.io/badge/status-stable-green.svg?style=flat-square)](https://github.com/LenFaki/EnerPrice)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Custom%20Integration-18BCF2?logo=homeassistant&logoColor=white&style=flat-square)](https://www.home-assistant.io/)
[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=LenFaki&repository=home-assistant-nl-day-ahead-prices&category=integration)
[![Tests](https://github.com/LenFaki/EnerPrice/actions/workflows/ci.yml/badge.svg)](https://github.com/LenFaki/EnerPrice/actions/workflows/ci.yml)
[![HACS validation](https://github.com/LenFaki/EnerPrice/actions/workflows/hacs.yml/badge.svg)](https://github.com/LenFaki/EnerPrice/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/LenFaki/EnerPrice/actions/workflows/hassfest.yml/badge.svg)](https://github.com/LenFaki/EnerPrice/actions/workflows/hassfest.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20EnerPrice-FF5E5B?style=flat-square&logo=ko-fi&logoColor=white)](https://ko-fi.com/lenfaki)

EnerPrice is a HACS-compatible Home Assistant custom integration for Dutch
dynamic energy prices.

Previously known as **NL Day Ahead Prices**, EnerPrice provides day-ahead
electricity prices, quarter-hour prices, supplier-specific all-in tariffs,
price analysis, best periods, trends, forecasts and energy advice.

Slimme day-ahead stroomprijzen, all-in tarieven, kwartierprijzen, analyses en
energieadvies voor Home Assistant.

## Previously Known As NL Day Ahead Prices

EnerPrice was previously named **NL Day Ahead Prices**. The GitHub repository is now `LenFaki/EnerPrice`, while the Home Assistant
integration domain remains `nl_day_ahead_prices` for backwards compatibility. Existing v1.x unique IDs,
entity IDs, automations, and dashboards are not renamed by this release.

It is inspired by the sensor and ApexCharts attribute shape of `hass-entso-e`, but it avoids depending on ENTSO-E by default. Prices are fetched from alternative providers with automatic fallback:

1. Nord Pool
2. Energy-Charts
3. Optional ENTSO-E fallback
4. Last known valid prices cache

The default bidding zone is `NL`, currency is `EUR`, and all sensor prices are exposed as `EUR/kWh`. Provider prices in `EUR/MWh` are converted automatically.

## EnerPrice 2.6 — Daily Energy Advisor

The optional Daily Energy Advisor finds a cheapest fully priced period for a configured flexible load. Optional Forecast.Solar, P1 and solar-power inputs enable a **separate conditional solar scenario**, including the opportunity cost of foregone export. Opt-in morning briefings require explicit consent; optional cumulative energy counters support conservative forecast-versus-actual checks. No devices are switched automatically.

**Start simple:** enable Daily Advisor, enter the appliance power and duration, and check the grid-only recommendation first. Add solar inputs, notifications and performance checks only when useful.

See [dashboard screenshots](docs/enerprice-v2-6-user-guide.md#dashboard-examples) and the [step-by-step EnerPrice 2.6 guide (English)](docs/enerprice-v2-6-user-guide.md) or the [Nederlandse handleiding](docs/enerprice-v2-6-gebruikershandleiding.md) for configuration, interpretation and troubleshooting.

## EnerPrice v2.5 — Smart Setup and Smart Energy Advisor

V2.5 adds optional **Smart Setup** to the existing Smart Energy Advisor. In the EnerPrice options you can store the grid/P1 entity, actual solar-production entity, gas-price entity, one flexible load and its power, efficiencies, gas energy content, surplus threshold and advice language. The advisor then uses these values automatically. Explicit service-call values always take precedence. Smart Setup remains disabled by default for existing installations, preserving v2.4.x behavior.

The Smart Energy Advisor

V2.4 adds a vendor-neutral Smart Energy Advisor for flexible loads and domestic
hot water. The new response service
`nl_day_ahead_prices.get_smart_energy_advice` can combine EnerPrice all-in
electricity prices with optional Home Assistant entities for solar production,
grid power and the current gas price.

For heat-source decisions, EnerPrice compares **useful heat cost**, not unlike
units directly. Grid-electric heat is calculated from EUR/kWh electricity and
the configured electric efficiency. Gas heat is calculated from EUR/m³, gas
energy content and the configured gas-appliance efficiency. The default gas
energy-content assumption is configurable and should be adjusted when a more
appropriate local or contract value is known.

The advisor can return `solar_surplus`, `cheap_grid`, `gas`, `wait` or
`normal`. It also looks ahead at the available day-ahead electricity prices,
so a flexible hot-water load can be advised to wait when a materially cheaper
electric interval is approaching. Direct numeric values can be supplied instead
of Home Assistant entities for testing and automations.

When grid/P1 power is available, measured export is used as the surplus source. Without grid data, actual solar production may be used only as an explicit fallback; the response identifies `surplus_source` as `measured_grid_export`, `solar_production_fallback` or `unknown`. Missing and unavailable inputs are not silently treated as zero.

For partial surplus, `effective_electric_heat_cost_per_kwh` uses the `purchased_energy_only` model: only the remaining grid share is charged at the current all-in purchase price. Lost feed-in value/opportunity cost of self-consumed energy is not included, so this is not a complete economic cost model.

EnerPrice remains advisory: it does **not** switch a boiler, heat source or
other appliance directly. Home Assistant automations can use the response to
control a Solyx Nemo or any other compatible flexible load once that device is
available in Home Assistant.

Example:

```yaml
action: nl_day_ahead_prices.get_smart_energy_advice
data:
  gas_price_entity: sensor.your_current_gas_price
  solar_power_entity: sensor.your_solar_power
  grid_power_entity: sensor.your_grid_power
  gas_efficiency: 0.90
  electric_efficiency: 1.0
  language: en
response_variable: energy_advice
```

If more than one EnerPrice config entry is loaded, pass `config_entry_id` to select the configuration whose all-in prices should be used.

A complete Dutch README is available in [README.nl.md](README.nl.md).

## EnerPrice v2.3

### Price Advisor

`sensor.nl_day_ahead_price_advisor` combines current all-in price, price score,
rating, trend, and volatility into `excellent`, `good`, `neutral`, `avoid`, or
`critical` advice. It also looks ahead for a materially cheaper interval and
exposes the next better time and price, minutes to wait, percentage savings for
positive prices, the best upcoming price, and a concise dashboard summary.
Negative prices and quarter-hour intervals are handled explicitly.

### Price Score

`sensor.nl_day_ahead_price_score` reports a robust `0-100` score where `100` is
the cheapest available price and `0` the most expensive. It uses today plus
tomorrow when available, supports negative prices, and exposes percentile,
minimum, maximum, average, and a human-readable score label.

The optional Today Score and Tomorrow Score sensors summarize an entire day,
including volatility, cheapest 1/2/4-hour blocks, the most expensive hour, and
negative/cheap/expensive minutes.

### EV Charging Planner

Call `nl_day_ahead_prices.find_best_charging_window` with required energy,
charger power, and a deadline. It supports consecutive or split sessions,
minimum session length, all-in or market prices, and both hourly and
quarter-hour prices. The response includes timing, selected intervals, energy,
cost, and alternatives.

### Boiler Planner

`nl_day_ahead_prices.find_best_heating_window` plans a boiler or heat-pump
boiler for the cheapest requested duration, optionally before the price peak.

Example: set `duration_minutes: 120` and a timezone-aware deadline at 07:00 to
heat a boiler for two hours before morning use. Use `180` minutes to select the
cheapest three-hour heat-pump boiler period.

### Battery Strategy

`nl_day_ahead_prices.find_battery_strategy` considers capacity, state of
charge, charge/discharge power, SOC limits, round-trip efficiency, grid
charging, and export. Arbitrage is recommended only when the price spread
covers efficiency loss and a safety margin.

### Solar Export Advisor

The Export Advisor, Best Export Period, and Worst Export Period sensors use the
supplier profile's export and feed-in fees. The
`nl_day_ahead_prices.find_best_export_window` service can also estimate export
revenue for a specified amount of energy.

### Appliance Planner

`nl_day_ahead_prices.find_best_appliance_window` plans flexible loads such as a
washing machine, dishwasher, dryer, swimming-pool pump, or underfloor heating.
It returns the recommended period, expected cost, reason, and an editable
automation example.

### Dashboard Generator

`nl_day_ahead_prices.generate_dashboard_yaml` creates CasaRegie-inspired
compact, full, or energy-advisor Lovelace YAML with native Home Assistant
Sections, headings, tiles and Advisor Markdown. Richer layouts can include
ApexCharts. When called from Home Assistant, the generator resolves the actual
EnerPrice entity IDs from the Entity Registry, including renamed or prefixed
entities. Copy the returned YAML manually into a dashboard. ApexCharts Card
must already be installed when using a layout with the graph.

### Automation Generator

`nl_day_ahead_prices.generate_automation_yaml` creates editable examples for
boilers, EV charging, cheap/expensive notifications, batteries, and appliances.
Review entity IDs and planner settings before enabling generated YAML.

### Example Automations

The existing examples later in this README remain valid. V2 notification
helpers add these optional, disabled-by-default triggers:

```yaml
automation:
  - alias: Notify exceptional energy opportunity
    triggers:
      - trigger: state
        entity_id: binary_sensor.nl_day_ahead_exceptional_opportunity_now
        to: "on"
    actions:
      - action: notify.notify
        data:
          message: "EnerPrice found an exceptional energy opportunity."
```

### Backwards Compatibility

- Domain remains `nl_day_ahead_prices`.
- Existing v1.x sensor keys and unique IDs remain unchanged.
- Existing price attributes and ApexCharts formats remain available.
- The visible integration name is now EnerPrice.
- New advanced entities are disabled by default where appropriate.
- Supplier profile v1 fields are migrated in memory to the v2 profile schema.

Release notes are available in [CHANGELOG.md](CHANGELOG.md) and on the GitHub
releases page.

Maintainers: see [the release workflow](docs/releases.md) for automatic releases
after successful main CI, version-bump requirements and manual recovery.

## Installation

### HACS

EnerPrice is currently installable as a HACS custom repository. Official HACS
default-repository inclusion is being prepared; until that submission is
accepted, add the repository manually as described below.

1. Add this repository as a custom repository in HACS.
2. Select category `Integration`.
3. Install **EnerPrice**.
4. Restart Home Assistant.
5. Add the integration from **Settings > Devices & services**.

Important: the HACS `update.nl_day_ahead_prices_update` entity only belongs to
HACS itself. It confirms that the custom repository is installed and can be
updated. The actual price sensors are only created after you add **EnerPrice**
from **Settings > Devices & services > Add integration**.

### Manual

Copy `custom_components/nl_day_ahead_prices` into your Home Assistant `custom_components` directory and restart Home Assistant.

## Configuration

The config flow defaults to:

- Country / bidding zone: `NL`
- Currency: `EUR`
- Primary provider: Nord Pool
- ENTSO-E fallback: disabled

The integration uses Home Assistant's shared `aiohttp` websession, modern async config entries, options flow, and a `DataUpdateCoordinator`. API requests run in the background and do not block startup. Regular API refreshes run hourly at minute 7; interval-boundary entity updates do not request new data.

Tomorrow prices are optional. Before publication, valid today prices remain
available. If all providers fail after midnight, cached tomorrow intervals are
promoted to today using their Europe/Amsterdam timestamps. Partial current-day
cache data is accepted; stale days are rejected. Diagnostics report fetch
statuses, provider errors, and cache rollover status.

Nord Pool can publish Dutch day-ahead prices in 15-minute market time units.
This integration preserves those source intervals and exposes hourly or
quarter-hour prices depending on the selected price interval option.

## Troubleshooting

### I only see `update.nl_day_ahead_prices_update`

That entity is created by HACS, not by this integration. Complete the Home
Assistant setup flow:

1. Restart Home Assistant after installing or updating from HACS.
2. Go to **Settings > Devices & services**.
3. Select **Add integration**.
4. Search for **EnerPrice**. Older cached frontend data may still show
   **NL Day Ahead Prices** until Home Assistant is restarted.
5. Finish the setup form with the default `NL` zone.

After that, the integration should create sensors such as
`sensor.nl_day_ahead_prices_current_market_price` and
`binary_sensor.nl_day_ahead_prices_tomorrow_prices_available`.

If **EnerPrice** is not listed in **Add integration**, Home Assistant
has not loaded the custom component yet. Restart Home Assistant and check
**Settings > System > Logs** for `nl_day_ahead_prices`.

## Options

The all-in price can be configured in the integration options. Choose one of
the built-in Dutch dynamic supplier profiles, or select **Custom supplier** and
enter your own values.

- `selected_supplier`: default `Zonneplan`
- `supplier_tariff_updates`: **Automatic (recommended)** or **Bundled only**; default Automatic
- `price_resolution`: default `auto`
- `energy_tax`: default `0.1108` EUR/kWh, including VAT
- `vat`: default `0.21`

Automatic retrieves the centrally maintained, validated EnerPrice tariff registry,
at most once per 24 hours. It does not scrape supplier websites. Bundled only uses
the installed tariff data and ignores remote cache for that configuration. Mode
changes apply immediately without restarting HA or fetching market prices.
Multiple configurations share one updater; an Automatic configuration does not
override another configuration's Bundled-only choice. Custom fees always win.

Two enabled diagnostic entities on the existing EnerPrice device show
**Supplier Tariff Status** (freshness and provenance) and **Supplier Registry
Status** (`remote`, `cached_remote` or `bundled`). They remain available when
market APIs fail. See [registry modes and troubleshooting](docs/supplier-tariff-registry.md#home-assistant-ux-pr2).

When you select a built-in supplier, the next screen shows the profile values
that will be used for the all-in calculation: purchase fee, monthly fee,
verification date, and source URL. When you select **Custom supplier**, the next
screen shows the custom tariff fields:

- `custom_supplier_name`
- `custom_monthly_fee_electricity`
- `custom_purchase_fee_electricity`
- `custom_purchase_fee_includes_vat`
- `custom_sell_fee_electricity`
- `custom_sell_fee_includes_vat`

Formula:

```text
all_in = market_price * (1 + vat) + energy_tax + supplier_purchase_fee_incl_vat
```

When a supplier profile marks the purchase fee as excluding VAT, the integration
uses `purchase_fee_electricity * (1 + vat)` first.

The market price from the day-ahead providers is a bare wholesale price. The
integration applies VAT to that market price before adding energy tax and the
supplier purchase fee.

`Current Market Price` and `Next Market Price` are bare market prices.
They do not include taxes, VAT, or supplier purchase fees.

The day summary sensors are all-in prices using your configured tax and
supplier settings:

- `Average Price Today`
- `Average Price Tomorrow`
- `Lowest Price Today`
- `Highest Price Today`

Use `Current All-in Price` and `Next All-in Price` for all-in prices for the
current and next interval.

## Hourly And Quarter-Hour Prices

Some suppliers settle electricity per hour, while others use quarter-hour
prices. The integration can expose either format:

- `auto`: choose the resolution from the selected supplier profile.
- `hourly`: always expose hourly prices.
- `quarter_hour`: always expose quarter-hour prices.

Zonneplan uses hourly prices until 2026-07-31 and quarter-hour prices from
2026-08-01. In `auto` mode this switch is handled from the supplier profile.

When converting quarter-hour prices to hourly prices, the integration uses the
average of the available quarter-hour entries in that hour. When converting
hourly prices to quarter-hour prices, each quarter receives the same price as
the source hour.

The original source prices remain available through:

- `raw_prices`
- `raw_prices_today`
- `raw_prices_tomorrow`
- `raw_price_resolution`

### Supplier Tariffs

Market/day-ahead prices are fetched automatically. Supplier fees are separate:
EnerPrice bundles an offline tariff registry in `supplier_tariffs.json`, with
validity periods, sources and verification dates. The legacy
`supplier_profiles.json` remains a fallback for compatibility.

Supplier tariffs are periodically verified against published supplier
information. EnerPrice exposes the verification date and source so users
can assess tariff freshness. Some retained values still require verification;
the profiles are not guaranteed to match every individual contract.

Core sensor attributes (including Selected Supplier) expose
`supplier_tariff_status`, `supplier_tariff_valid_from`,
`supplier_tariff_valid_until`, `supplier_tariff_last_verified`,
`supplier_tariff_source_type`, `supplier_tariff_source_url`,
`supplier_tariff_age_days`, and `supplier_settlement_resolution`.
Existing `supplier_purchase_fee` and `supplier_monthly_fee` remain available.

Freshness is `current` through 60 days, `verification_recommended` through 120
days, and `stale` thereafter; missing verification is `unknown`. These status
attributes warn about freshness without disabling prices. Custom supplier
settings take precedence and report `custom`. Tibber's new standard monthly
fee may not apply to older contracts; enter your own contract values when needed.

In Automatic mode, EnerPrice can retrieve the centrally maintained validated
tariff registry at runtime, at most once per 24 hours. Bundled-only mode never
uses the remote registry. See the
[registry architecture and source review](docs/supplier-tariff-registry.md)
for schema, VAT semantics, source limitations and future fallback order.

Supplier-specific tariffs are stored in the bundled registry rather than
hard-coded inside Python. The compatibility profile exposes:

- display name
- monthly electricity fee
- electricity purchase fee
- whether the purchase fee includes VAT
- electricity sell fee
- whether the sell fee includes VAT
- `last_verified`
- `source_url`

Bundled profiles currently include Zonneplan, Tibber, ANWB Energie,
EasyEnergy, Eneco, Vandebron, Vattenfall, Greenchoice, EnergyZero, SamSam,
Pure Energie, and Custom supplier.

Tariffs can change and may differ per contract. Always check your current
supplier contract or tariff sheet. The bundled `last_verified` and `source_url`
fields are only there to make the included profile data auditable.

Use the settings like this:

- `energy_tax`: Dutch energy tax in `EUR/kWh`, including VAT.
- `vat`: VAT fraction, for example `0.21`.
- `custom_purchase_fee_electricity`: supplier purchasing fee in `EUR/kWh`.
- `custom_purchase_fee_includes_vat`: enable this when your supplier publishes
  the fee including VAT.

If your supplier publishes a fee in `EUR/MWh`, convert it to `EUR/kWh`:

```text
EUR/kWh = EUR/MWh / 1000
```

Fixed monthly subscription fees are not included automatically in
`Current All-in Price`, because they are not a per-kWh market price component.
If you want to include them anyway, convert them manually to an estimated
`EUR/kWh` value based on your expected monthly consumption and add that to the
custom purchase fee.

## Entities

Sensors:

- Current Market Price
- Next Market Price
- Average Price Today
- Average Price Tomorrow
- Lowest Price Today
- Lowest Energy Price Time
- Highest Price Today
- Highest Energy Price Time
- Time Of Lowest Price Today
- Time Of Highest Price Today
- Current All-in Price
- Next All-in Price
- Average All-in Price Today
- Lowest All-in Price Today
- Highest All-in Price Today
- Supplier Purchase Fee
- Supplier Monthly Fee
- Selected Supplier
- Effective Price Resolution
- Current Provider
- Last Successful Update

Binary sensor:

- Tomorrow Prices Available
- API/Data Available
- Best Price Period (disabled by default)
- Peak Price Period (disabled by default)

### Energy Optimization Toolkit

Advanced analysis entities are disabled by default. Enable only the entities
you need from the integration's device page.

- All-in forecasts for the next 1, 2, 3, 4, 6, 8, 12, and 24 hours.
- Trend values from `strongly_falling` through `strongly_rising`, including
  `trend_value_percent` and the next trend change.
- Three-level (`low`, `normal`, `high`) and five-level price ratings.
- Volatility for today, tomorrow, and the next 24 hours with min, max, average,
  median, span, and percentage attributes.
- Best and peak period start, end, remaining time, progress, and next start.

The calculations work from actual timestamps and therefore support hourly,
quarter-hour, and 23/25-hour daylight-saving days without fixed interval
counts. The toolkit was independently implemented, with
[hass.tibber_prices](https://github.com/jpawlowski/hass.tibber_prices) credited
as product inspiration.

### Runtime Settings

The device page contains `number` controls for best/peak duration, flexibility,
minimum gap, and trend thresholds. Switches control period relaxation,
extended attributes, and chart helpers. Changes apply immediately without a
Home Assistant restart. Enabling chart helpers adds the calculated
`best_periods` and `peak_periods` arrays used by the generated ApexCharts card.

### Services

`nl_day_ahead_prices.export_chart_data` returns `{time, price}` data for market
or all-in prices. It supports today/tomorrow selection, automatic/hourly/
quarter-hour resolution, and optional best/peak period output.

`nl_day_ahead_prices.generate_apexcharts_config` returns a YAML string with
market, all-in, cheapest-period, and peak-period series. Call response services
from Developer Tools using the UI's response-data option.

## Attributes

Each price sensor exposes ApexCharts-friendly attributes:

- `prices`
- `prices_today`
- `prices_tomorrow`
- `all_in_prices_today`
- `all_in_prices`
- `all_in_prices_tomorrow`
- `raw_prices`
- `raw_prices_today`
- `raw_prices_tomorrow`
- `raw_price_resolution`
- `price_resolution`
- `requested_price_resolution`
- `effective_price_resolution`
- `resolution_converted`
- `raw_today`
- `raw_tomorrow`
- `provider`
- `fallback_used`
- `last_successful_update`
- `cache_used`
- `cache_age_minutes`
- `data_completeness`
- `current_interval_start`
- `current_interval_end`
- `next_interval_start`
- `market_price`
- `all_in_price`
- `rating_3_level`
- `rating_5_level`
- `trend`
- `trend_value_percent`
- `volatility`
- `day_min`, `day_max`, `day_average`, `day_median`
- `selected_supplier`
- `supplier_purchase_fee`
- `supplier_monthly_fee`
- `energy_tax`
- `vat`
- `supplier_profile_last_verified`
- `supplier_profile_source_url`

Price entries use this format:

```json
{
  "time": "2026-07-02T13:00:00+02:00",
  "price": 0.123456
}
```

## ApexCharts Example

| Attribute | Price data |
| --- | --- |
| `prices` | All available market prices |
| `prices_today` | Today's market prices |
| `prices_tomorrow` | Tomorrow's market prices |
| `all_in_prices` | All available all-in prices |
| `all_in_prices_today` | Today's all-in prices |
| `all_in_prices_tomorrow` | Tomorrow's all-in prices |

`all_in_prices` automatically includes tomorrow as soon as those prices are
available. For an all-in series in **ct/kWh**, use this recommended generator
and set the series unit to `ct/kWh` (the attributes themselves remain EUR/kWh):

```yaml
unit: ct/kWh
data_generator: |
  const prices = entity.attributes.all_in_prices ?? [];

  return prices.map(p => [
    new Date(p.time).getTime(),
    parseFloat((p.price * 100).toFixed(2))
  ]);
```

EnerPrice always exposes its core market and all-in price arrays as sensor
attributes. Extended analysis attributes can optionally be enabled separately
using the existing **Enable Extended Attributes** switch on the device page
(enabled by default). No options-flow change is needed for existing dashboards.

Use `entity.attributes.prices_today` for market prices and
`entity.attributes.all_in_prices_today` for all-in prices. Both retain the
`{time, price}` format, including when prices are restored from cache.

```yaml
type: custom:apexcharts-card
graph_span: 48h
span:
  start: day
now:
  show: true
  label: Now
header:
  show: true
  title: EnerPrice (EUR/kWh)
yaxis:
  - decimals: 3
series:
  - entity: sensor.nl_day_ahead_prices_average_price_today
    name: Market price
    stroke_width: 2
    float_precision: 4
    type: column
    opacity: 1
    data_generator: |
      return entity.attributes.prices.map((entry) => {
        return [new Date(entry.time), entry.price];
      });
```

For quarter-hour prices, the same data generator works:

```yaml
data_generator: |
  return entity.attributes.prices.map((entry) => {
    return [new Date(entry.time), entry.price];
  });
```

For today only:

```yaml
data_generator: |
  return entity.attributes.prices_today.map((entry) => {
    return [new Date(entry.time), entry.price];
      });
```

## Automation Examples

Run a boiler only during a selected best period:

```yaml
automation:
  - alias: Boiler during best price period
    triggers:
      - trigger: state
        entity_id: binary_sensor.nl_day_ahead_prices_best_price_period
        to: "on"
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.boiler
  - alias: Boiler off after best price period
    triggers:
      - trigger: state
        entity_id: binary_sensor.nl_day_ahead_prices_best_price_period
        to: "off"
    actions:
      - action: switch.turn_off
        target:
          entity_id: switch.boiler
```

Start a washing machine at the cheapest two-hour block by setting **Best Period
Duration** to `120` minutes and triggering on **Best Price Period**. For an
expensive-period notification:

```yaml
automation:
  - alias: Peak price warning
    triggers:
      - trigger: state
        entity_id: binary_sensor.nl_day_ahead_prices_peak_price_period
        to: "on"
    actions:
      - action: notify.notify
        data:
          message: "The peak electricity price period has started."
```

The ApexCharts example above can show both market and all-in price by adding a
second series using `all_in_prices_today` and `all_in_prices_tomorrow`.

## Migration From hass-entso-e

This integration keeps the key ApexCharts attribute format compatible with `hass-entso-e`: `entity.attributes.prices` remains an array of `{ time, price }` objects.

Main differences:

- Domain changes from `entsoe` to `nl_day_ahead_prices`.
- Entity IDs will be newly generated by Home Assistant.
- Prices are always normalized to `EUR/kWh`.
- ENTSO-E is optional and only used as a fallback when enabled with an API token.
- The old template-based cost modifier is replaced by explicit all-in price options for Dutch tax, supplier profiles, and VAT.

After installing, update automations and ApexCharts cards to point at the new entity IDs while keeping the same `attributes.prices` data generator pattern.

## V2.5 Smart Setup quick validation

After enabling **Smart Setup**, EnerPrice creates a persistent **Smart Energy Advisor** sensor. It refreshes with EnerPrice price updates and when configured grid/P1, solar-production or gas-price entities change. The generated Smart Energy dashboard uses the actual Entity Registry ID for this sensor.

Before relying on the advice, verify the configured inputs in Home Assistant: grid power is in W with positive = import and negative = export, solar production is in W, and gas price is in EUR/m³. Missing or unavailable inputs remain unknown rather than being treated as zero. Smart Setup supports one flexible load in v2.5 and remains advisory only; EnerPrice does not switch the load.

For a practical verification sequence and dashboard examples, see [Dashboard recipes](docs/dashboard-recipes.md). For the final real-installation release gate, use [the v2.5 Home Assistant validation checklist](docs/v2.5-ha-validation.md).
