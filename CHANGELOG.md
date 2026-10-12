# Changelog

## 2.6.0 - 2026-10-12

### Added
- Optional Daily Energy Advisor with Forecast.Solar today/tomorrow totals, explicit data-quality states and three-day outlook; unavailable future totals/prices remain unknown.
- Grid-only best consumption windows and separately labelled conditional solar scenarios using existing all-in prices and export opportunity costs.
- Opt-in daily dashboard cards, Daily Energy Advice and Best Consumption Window entities, with NL/EN labels and built-in-card fallback.
- Explicitly consented Home Assistant morning briefings with selectable notification target, local delivery time and durable at-most-once attempts across restarts and DST.
- Conservative forecast-versus-actual energy performance checks using cumulative meters, with bounded local history and dedicated status entities.

### Compatibility and limitations
- Existing installations retain settings, entity IDs, price calculations, services and default dashboards; Daily Advisor remains optional.
- No automatic device control, email, additional paid API or unsupported claim of realized financial savings.
- Missing, stale, reset or incomplete readings never count as zero or verified performance; live Home Assistant smoke testing is recommended before publishing.
- See docs/daily-advisor.md, docs/daily-advisor-pr2.md and docs/daily-advisor-pr3.md for configuration and rollback guidance.

## 2.5.4 - 2026-10-01

### Fixed
- Fix the v2.5.3 Home Assistant startup regression caused by an invalid `CONFIG_SCHEMA` declaration.
- Restore normal EnerPrice config-entry setup so price entities and generated dashboards receive price data again.

### Compatibility
- No pricing logic, entity IDs, services, supplier tariffs or Smart Energy configuration are intentionally changed from v2.5.3.
- Existing EnerPrice configuration is preserved; no reconfiguration is required.

## 2.5.3 - 2026-10-01

### Improved
- Add official HACS repository validation and Home Assistant Hassfest validation workflows.
- Add an MIT license and repository validation badges.
- Align HACS metadata and Home Assistant selector translations with current validator requirements.
- Clarify in the English and Dutch installation documentation that EnerPrice remains a custom HACS repository until the default-repository submission is accepted.

### Compatibility
- No runtime behavior, entity IDs, services or Smart Energy configuration are intentionally changed from v2.5.2.
- EnerPrice remains advisory and does not automatically switch connected devices.

## 2.5.2 - 2026-10-01

### Fixed
- Ignore disabled or stale Entity Registry entries when generating dashboards, preventing optional cards from being generated for entities that Home Assistant reports as `Entiteit niet gevonden`.
- Keep temporarily unavailable entities eligible for dashboards when they still have a valid Home Assistant State object.

### Improved
- Cache shared serialized price arrays once per coordinator analysis cycle instead of rebuilding them independently for every EnerPrice sensor, reducing repeated work during state updates.
- Target the slow `today_score` state-update warning observed during real Home Assistant validation without removing existing price attributes or dashboard compatibility.

### Compatibility
- Existing EnerPrice entities, services, integration domain and v2.5.1 configuration remain compatible.
- EnerPrice remains advisory and does not automatically switch connected devices.

## 2.5.1 - 2026-10-01

### Fixed
- Refresh Smart Energy Advisor state safely on the Home Assistant event loop when configured P1/grid, solar or gas entities change, avoiding thread-safety warnings and stale live measurements.
- Preserve Smart Setup entity selections, efficiencies, flexible-device settings and advice language when Smart Setup is disabled and later re-enabled.
- Treat tiny measured grid export below the configured solar-surplus threshold as insignificant instead of reporting a misleading partial surplus.
- Generate Smart Energy dashboards without guessed optional Entity Registry IDs, preventing `Entiteit niet gevonden` cards when optional entities are unavailable or disabled.
- Hide missing advisor copy instead of rendering a literal `None` value.

### Improved
- Rename flexible-load wording to the clearer `flexibel apparaat` / `flexible device` terminology.
- Add field-level Smart Setup guidance for P1 sign convention, units, gas-price input, efficiencies, solar measurements and flexible-device power.
- Clarify that a separate solar-production sensor is optional when usable P1/grid measurements are available and that multiple inverter sensors can be combined with a Home Assistant template sensor.
- Expand regression coverage for Smart Setup lifecycle behavior, dashboard generation and meaningful solar-surplus thresholds.

### Compatibility
- Existing EnerPrice entities, services, integration domain and v2.5.0 configuration remain compatible.
- EnerPrice remains advisory and does not automatically switch connected devices.


## 2.5.0 - 2026-10-01

### Added
- Add optional Smart Setup for storing grid/P1 power, solar production, gas price, one flexible load, efficiencies, gas energy content, surplus threshold and advice language.
- Add a persistent Smart Energy Advisor sensor that combines live Home Assistant inputs with EnerPrice all-in prices and refreshes when configured grid, solar or gas entities change.
- Add an adaptive Smart Energy dashboard generator that resolves actual Entity Registry IDs and includes only the Smart Setup inputs that are configured.
- Add Dutch and English Smart Energy dashboard labels, current advice, useful-heat costs and next-better-price timing.
- Add practical Home Assistant release-validation documentation for Smart Setup, hourly/quarter-hour prices and generated dashboards.

### Improved
- Reuse stored Smart Setup defaults in the Smart Energy Advisor service while keeping explicit service-call values authoritative.
- Reload the config entry when Smart Setup is enabled/disabled or its live input entity IDs change, preventing missing advisor entities and stale state listeners.
- Preserve missing or unavailable live measurements as unknown instead of silently treating them as zero.
- Expand regression coverage for the Smart Energy dashboard contract, zero-versus-unknown measurements and Smart Setup lifecycle changes.

### Compatibility
- Smart Setup remains disabled by default for existing installations.
- EnerPrice remains advisory and does not automatically switch boilers, EV chargers, batteries or other devices.
- V2.5 supports one flexible load in Smart Setup; device-specific optimization and multi-load orchestration remain outside this release.
- Existing EnerPrice entities, services, integration domain and v2.4.x Smart Energy Advisor calls remain compatible.


## 2.4.1 - 2026-10-01

### Improved
- Register EnerPrice services once at integration-level setup instead of tying service availability to a loaded config entry.
- Add optional `flexible_load_power_w` to Smart Energy Advisor so measured surplus can be compared with the actual flexible-load demand.
- Distinguish full and partial surplus, expose surplus coverage, and include the remaining grid share in effective electric heat cost comparisons.
- Add English and Dutch copy-ready dashboard/setup recipes as groundwork for a future guided onboarding wizard.

### Compatibility
- Existing Smart Energy Advisor calls without `flexible_load_power_w` keep the v2.4.0 surplus-threshold behavior.
- EnerPrice remains advisory and does not directly switch user devices.
- Existing service names and response fields remain available.

## 2.4.0 - 2026-09-30

### Added
- Generic Smart Energy Advisor response service combining EnerPrice all-in electricity prices with optional Home Assistant gas-price, solar-production and grid-power entities.
- Useful-heat cost comparison between grid-electric and gas heating, with configurable electric efficiency, gas efficiency and gas energy content.
- Solar-surplus priority based on measured grid export when available, separate solar-production context, and forward-looking `wait` advice when a materially cheaper electricity interval is approaching.
- Direct numeric service inputs for testing and advanced automations without vendor-specific dependencies.\n- Optional `config_entry_id` selection for Smart Energy Advisor use with multiple EnerPrice configurations.\n- English and Dutch version-independent README header artwork.
- Expanded Dutch README (`README.nl.md`) with an English/Nederlands language switch and documentation for planners, services, runtime settings, chart attributes and migration.

### Safety and compatibility
- EnerPrice remains advisory and does not directly switch boilers or other loads.
- Existing entities, services, tariff calculations and the `nl_day_ahead_prices` domain remain unchanged.
- Smart Energy Advisor inputs are optional; existing installations require no new configuration.\n- Added edge-case coverage for measured solar surplus, equal heat costs, zero gas price and the exact future-price threshold.


All notable changes to **EnerPrice** are documented here.

## 2.3.0

### Added

- Extended the Price Advisor with future price context, including the next materially cheaper interval, time until that interval, the next better price, percentage savings for positive prices, and the best upcoming price.
- Added concise Dutch and English Advisor summaries for dashboards and practical flexible-load decisions.
- Added CasaRegie-inspired compact, full, and energy-advisor dashboard layouts using native Home Assistant Sections, headings, tiles, and Markdown, with ApexCharts in the richer layouts.
- Generated dashboards now resolve the actual EnerPrice entity IDs from the Home Assistant Entity Registry, so renamed or prefixed entities can be used automatically.

### Improved

- Made Advisor timing resolution-aware for hourly and quarter-hour prices and safe for negative prices.
- Made generated Advisor Markdown resilient when entities are temporarily unknown or unavailable.
- Added regression coverage for future-price advice, negative prices, quarter-hour wait times, compact/full dashboard output, and renamed Home Assistant entities.

### Compatibility

- Existing entity IDs, unique IDs, services, price calculations, supplier tariffs, and public price-array schemas remain compatible.
- The integration domain remains `nl_day_ahead_prices`.
- EnerPrice continues to provide advice and generated YAML; it does not directly switch user devices.

## 2.2.3

### Improved

- Reused shared all-in price data across sensor state and attribute updates to reduce repeated supplier-price calculations.
- Cached trend, rating, volatility and best/peak-period analysis within a coordinator cycle when inputs are identical.
- Avoided unnecessary trend calculations for price-rating sensors.
- Added deterministic regression coverage to ensure price trajectory state and attributes reuse expensive analysis work.

### Compatibility

- No supplier tariff amounts or price formulas changed.
- Existing entity IDs, public sensor attributes, units and analysis semantics remain unchanged.
- The integration domain remains `nl_day_ahead_prices`.

## 2.2.2

### Improved

- Reduced repeated all-in price calculations during advanced analysis sensor state updates by reusing the coordinator analysis cache.
- Shared the cached all-in price list across price rating, price level, forecast, trend, volatility and related analysis updates within the same coordinator cycle.

### Compatibility

- No supplier tariff amounts or price formulas changed.
- Existing entity IDs, public sensor attributes and analysis semantics remain unchanged.
- The integration domain remains `nl_day_ahead_prices`.

## 2.2.1

### Fixed

- Preserved the remote supplier registry's last check result across Home Assistant restarts, including backward-compatible recovery from v2.2.0 cache data.
- Prevented large price and chart arrays from being written to Recorder history while keeping the same live entity attributes available to dashboards and ApexCharts.
- Kept the Average All-in Price Today statistics metadata explicitly on `EUR/kWh` with regression coverage.

### Improved

- Clarified supplier registry metadata by exposing the schema version and active registry revision separately while retaining the legacy `supplier_registry_version` compatibility attribute.
- Added regression coverage for remote-registry restart state and recorder-safe chart attribute compatibility.

### Compatibility

- No supplier tariff amounts or price calculations changed.
- Existing chart/ApexCharts attribute names and live data remain available.
- The integration domain remains `nl_day_ahead_prices`.

## 2.2.0

### Added

- Added a validated remote supplier tariff registry with persistent last-known-good caching and the bundled registry as an offline fallback.
- Added `Supplier Tariff Status` and `Supplier Registry Status` diagnostic sensors for tariff freshness, registry provenance and update state.
- Added per-config-entry `Automatic` and `Bundled only` supplier tariff update modes, including runtime switching without a Home Assistant restart or an extra market-price API request.
- Added safe Home Assistant diagnostics for supplier tariff and registry metadata.
- Added deterministic supplier-registry maintenance tooling with console, JSON and Markdown audit reports.
- Added a weekly GitHub supplier tariff audit that maintains one central issue for records requiring human verification.
- Added a semantic registry revision guard so tariff changes require a higher registry revision.

### Improved

- Shared the pure-Python registry validation rules between the Home Assistant runtime and maintainer tooling.
- Remote registry checks validate complete candidates before activation, persist before publication and keep cached or bundled data active when a remote check fails.
- Multiple EnerPrice config entries can independently use automatic or bundled tariff data while sharing one remote transport/cache manager.
- Supplier tariff freshness uses the existing 60/120-day verification policy and preserves date-aware historical tariff selection.
- Registry maintenance remains read-only: supplier websites are not scraped and tariff values are never changed automatically.

### Compatibility

- The integration domain remains `nl_day_ahead_prices`.
- Existing price entities, services, configuration and price-array schemas remain compatible.
- Custom supplier configuration retains precedence over registry supplier data.
- The bundled supplier registry remains available for offline use and as fallback.

## 2.1.1

### Fixed

- Corrected the current SamSam import purchase fee to EUR 0.021118/kWh including VAT; retained its historical tariff.
- Corrected easyEnergy settlement to quarter-hour prices and confirmed its VAT-inclusive import and monthly fees.

### Improved

- Reviewed Vattenfall and conservatively retained its legacy tariff because a complete replacement tariff, export-fee mapping and effective date could not be established.
- Reviewed official supplier sources on 2026-09-23, including ANWB's monthly fee and EnergyZero's VAT-exclusive fee calculation.
- Documented partial verification and export-model limitations; incomplete records retain conservative verification dates.
- Observation-date transitions do not claim a proven contract effective date. The registry remains bundled and offline-first.
- No changes to entity IDs, services, configuration or dashboard attribute schemas.

## 2.1.0

### Added

- Offline supplier tariff registry with schema validation, validity periods and deterministic date selection in Europe/Amsterdam.
- Tariff freshness, source, validity, settlement resolution and registry origin attributes and diagnostics.
- Compatibility adapter to existing supplier profiles and legacy fallback; custom options retain precedence.

### Changed

- Updated Tibber's published standard tariff from September 2026, easyEnergy import fee precision and EnergyZero's 2026 VAT-exclusive import fee.
- Added verified quarter-hour settlement for Tibber, EnergyZero and Greenchoice; preserved Zonneplan's existing dated migration.
- Kept unverified fees and their verification dates unchanged, with source quality and review limitations documented.
- Prepared a validation boundary for future remote registries without adding any network requests.

## 2.0.3

### Added

- Added `all_in_prices` as a combined all-in price array for all currently available intervals.
- ApexCharts dashboards can now use a single attribute for today and tomorrow all-in prices.

### Improved

- Combined price arrays preserve chronological order across the repeated DST hour.
- Made the market-price and all-in-price attribute APIs consistent:
  - `prices`
  - `prices_today`
  - `prices_tomorrow`
  - `all_in_prices`
  - `all_in_prices_today`
  - `all_in_prices_tomorrow`

## 2.0.2

### Fixed

- Restored core price attributes for ApexCharts and other dashboards.
- `prices_today`, `prices_tomorrow` and all-in price arrays are now always available when price data exists.
- Core dashboard attributes no longer depend on the extended-attributes setting.
- Added regression coverage for standard and quarter-hour chart data.

## v2.0.1

### Fixed

- Fixed morning price availability when tomorrow prices are not yet published.
- Fixed persistent cache rollover at midnight, including partial current-day data.
- Tomorrow-price failures no longer invalidate valid current-day prices.
- Improved Energy-Charts local-date handling for Europe/Amsterdam.
- Improved provider fallback diagnostics and scheduled hourly API refresh at minute 7.

### Added

- Added the Pure Energie supplier profile. Thanks to Henk van Donge for the
  contribution.

## v2.0.0 - EnerPrice branding

### Added

- New EnerPrice branding and central Price Advisor.
- Renamed the integration display name to EnerPrice.
- Added new logo and brand assets.
- Kept the integration domain `nl_day_ahead_prices` for backwards
  compatibility.
- Existing entities, services, and automations continue to work.
- Robust 0-100 Price Score, Today Score, and Tomorrow Score.
- EV charging, boiler, appliance, battery, and solar export planners.
- Energy Opportunity and optional cheap/expensive/opportunity binary sensors.
- Lovelace dashboard and automation YAML generators.
- Supplier profile schema v2 with import, export, feed-in, settlement, and
  capability metadata.
- Expanded diagnostics and cached v2 calculations.

### Compatibility

- The integration domain remains `nl_day_ahead_prices`.
- Existing v1.x entities, unique IDs, price attributes, and services remain
  available.
- New advanced entities are disabled by default where appropriate.

## v1.4.1 - 2026-07-09

### Changed

- Connected the chart helper switch to the calculated `best_periods` and
  `peak_periods` attributes.
- Made generated ApexCharts configurations tolerate disabled chart helpers.

## v1.4.0 - 2026-07-09

### Added

- Added all-in forecasts for the next 1, 2, 3, 4, 6, 8, 12, and 24 hours.
- Added trend, trajectory, three-level rating, five-level rating, and
  volatility analysis.
- Added configurable best and peak price periods, timing sensors, and binary
  sensors.
- Added live `number` and `switch` controls that apply without a restart.
- Added `export_chart_data` and `generate_apexcharts_config` response services.
- Added exact hour and quarter-hour boundary state updates without extra API
  polling.
- Added Home Assistant diagnostics and richer price attributes.
- Added analysis tests for hourly, quarter-hour, cache, and 23/25-hour DST days.

### Changed

- Persistent cache data now expires at the local date boundary and is rejected
  when today's prices are missing.
- Advanced analysis entities are disabled by default to keep the entity
  registry tidy.

## v1.3.0 - 2026-07-03

### Added

- Added `price_resolution` option: `auto`, `hourly`, or `quarter_hour`.
- Added automatic supplier-based price resolution.
- Added date-based Zonneplan support: hourly before 2026-08-01 and
  quarter-hour from 2026-08-01.
- Added raw source price attributes: `raw_prices`, `raw_prices_today`,
  `raw_prices_tomorrow`, and `raw_price_resolution`.
- Added converted price metadata attributes: `price_resolution`,
  `requested_price_resolution`, `effective_price_resolution`, and
  `resolution_converted`.
- Added `Effective Price Resolution` sensor.
- Added resolution-aware cheapest consecutive block attributes.

### Changed

- Nord Pool quarter-hour source prices are preserved and converted later based
  on the selected resolution.
- `Next Hour` price logic is now interval-aware and returns the next hour or
  next quarter-hour depending on the effective resolution.
- Updated Vandebron purchase and sell fee to `0.0257 EUR/kWh` including VAT.

### Fixed

- Fixed all-in price calculation by applying VAT to the bare market price.
  This addresses reported 1-3 ct/kWh differences compared to supplier apps.

## v1.2.2 - 2026-07-02

### Changed

- Improved the options flow for supplier profiles.
- The first options step now shows only provider, tax, VAT, and supplier
  selection.
- Built-in suppliers now show a confirmation screen with purchase fee, monthly
  fee, verification date, and source URL.
- Custom supplier fee fields are only shown when Custom supplier is selected.

## v1.2.0 - 2026-07-02

### Added

- Added supplier profiles in `supplier_profiles.json`.
- Added built-in profiles for Zonneplan, Tibber, ANWB Energie, EasyEnergy,
  Eneco, Vandebron, Vattenfall, Greenchoice, EnergyZero, SamSam, and Custom
  supplier.
- Added options flow fields for supplier selection, energy tax, VAT, and custom
  supplier fees.
- Added `Current All-in Price`, `Next Hour All-in Price`,
  `Average All-in Price Today`, `Lowest All-in Price Today`, and
  `Highest All-in Price Today`.
- Added `Supplier Purchase Fee`, `Supplier Monthly Fee`, and
  `Selected Supplier` sensors.
- Added all-in price attributes for today and tomorrow.
- Added supplier metadata attributes: selected supplier, purchase fee, monthly
  fee, energy tax, VAT, `last_verified`, and `source_url`.
- Added Dutch translations.

### Notes

- Supplier tariffs can change and may differ per contract. Always check your
  current supplier contract or tariff sheet.
- Fixed monthly supplier fees are exposed separately and are not automatically
  spread over kWh prices.

## v1.1.4 - 2026-07-02

### Added

- Added `Highest Energy Price Time`, a timestamp sensor for the most expensive
  hour today.

## v1.1.3 - 2026-07-02

### Changed

- Changed `Lowest Energy Price` compatibility sensor to expose the timestamp of
  the cheapest hour today.

## v1.1.2 - 2026-07-02

### Added

- Added `Lowest Energy Price` compatibility sensor.

## v1.1.1 - 2026-07-02

### Changed

- Changed daily summary sensors to use all-in prices where appropriate.

## v1.1.0 - 2026-07-02

### Added

- Added `Next Hour All-in Price`.

## v1.0.7 - 2026-07-02

### Added

- Added documentation for supplier tariff configuration.

## v1.0.6 - 2026-07-02

### Fixed

- Fixed options flow loading from the integration settings gear.

## v1.0.5 - 2026-07-02

### Fixed

- Aggregated Nord Pool 15-minute market time units into hourly averages.

## v1.0.0 - 2026-07-02

### Added

- Initial HACS-compatible custom integration.
- Added Nord Pool, Energy-Charts, optional ENTSO-E fallback, and last known
  valid prices cache.
- Added config flow, options flow, `DataUpdateCoordinator`, async provider
  fetching, sensors, binary sensor, tests, CI, and documentation.
