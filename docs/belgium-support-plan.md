# Belgium support (#81)

Phase 1 is implemented for the planned v2.7.0 release: **Belgian wholesale
market prices and consumption planning**. Belgian supplier contracts/all-in
prices are a separate future phase; selecting BE does not imply those exist.

## Setup / Instellen

In EnerPrice configuration or options, select **BE — wholesale only** and EUR.
Choose Nord Pool or Energy-Charts as primary provider. ENTSO-E is an optional
fallback requiring your own API token; both EIC domains are `10YBE----------2`.
All fallback providers keep the Belgian bidding zone. No Dutch fallback data
is substituted. Missing tomorrow prices are reported as unavailable.

**Nederlands:** kies **BE — alleen marktprijzen** en EUR. Belgische prijzen zijn
exclusief btw, belastingen, nettarieven en leverancierskosten. Dagelijks
financieel advies, all-inprijzen, terugleveropbrengsten en batterijrendement zijn
niet beschikbaar. Bestaande Nederlandse instellingen blijven bewaard voor
terugschakelen; ze worden in Belgische modus niet gebruikt.

`auto` keeps quarter-hour planning. An explicit hourly setting is respected.
Source resolution/conversion metadata shows if hourly source prices were
expanded rather than obtained as independent quarter-hour prices. NL and BE
currently share the same civil-time/DST rules; requests and tests cover the
Europe/Brussels local-day boundaries, including 23/25-hour days.

## Available scope

| Feature | BE mode |
| --- | --- |
| Current/next market price, daily average/min/max | EUR/kWh, excluding VAT and charges |
| Trends, ratings, volatility, best/peak periods | Relative wholesale-price comparisons |
| Appliance, EV charging and heating-window services | Market-price timing; costs are wholesale estimates |
| Chart export | `price_type: market` by default; explicit `all_in` rejected |
| Generated dashboard | Built-in cards, resolved live entity IDs, visible exclusions |
| Generated consumption-period automation | Actual market-period entity; review before enabling |
| All-in, supplier, export, Smart/Daily financial advice | Unavailable; unsupported services return an error |
| Battery return/export and contract-based notification templates | Unavailable |

Price attributes and service responses identify `market_country: BE`,
`pricing_mode: wholesale_only`, `price_basis: wholesale_ex_vat`, and
`taxes_and_supplier_fees_included: false`. No synthetic all-in/export arrays are
exposed. Existing unsupported entities become unavailable after changing mode;
regenerate your dashboard because old cards may still refer to NL entities.

The price cache records country/currency. Old unlabelled caches are treated as
NL/EUR; switching to BE cannot show those cached Dutch prices as Belgian ones.
Existing NL entity IDs and defaults remain unchanged.

## Services example

```yaml
action: nl_day_ahead_prices.find_best_appliance_window
data:
  appliance_name: Wasmachine
  duration_minutes: 120
  deadline: "2026-11-25T18:00:00+01:00"
  energy_kwh: 1.5
response_variable: enerprice_plan
```

The returned cost excludes VAT and every contract/network charge; only the
relative wholesale window is optimized. Generate a dashboard with
`nl_day_ahead_prices.generate_dashboard_yaml`; BE mode uses a market-only layout
regardless of NL-oriented card flags. No custom card installation is required.

## Validation and next phase

Synthetic provider-shaped tests cover both Belgian providers and ENTSO-E at
hourly and quarter-hour resolution on ordinary, 23-hour and 25-hour days,
negative prices, missing tomorrow data, fallback, cache isolation, configuration,
entity/service scope and generated dashboard IDs. These tests are not a claim
of Belgian household field validation or supplier-bill accuracy.

Future contract pricing requires verified tariff cards, index multipliers,
green-certificate contributions, injection formulas, network costs, regional
levies and VAT. Flanders, Wallonia and Brussels must be represented explicitly.
No generic Dutch tariff registry entry can stand in for that model.
