# Powerpeers dynamic electricity — supplier onboarding (#97)

Status: **draft research, not yet a selectable supplier profile**.

## Verified from published material

- Supplier name: Powerpeers (Netherlands).
- Dynamic import procurement fee: **EUR 0.01 per kWh** (published on the
  official Powerpeers website). Its VAT inclusion is not explicit on that
  page; do **not** infer it.
- Market price: EPEX day-ahead, quarter-hour settlement from 1 October 2025,
  with hourly fallback if quarter-hour data is unavailable.
- Export uses a **separate selling fee**, not necessarily the import fee.
  Contract terms state that the actual amount is shown in the customer's
  dashboard. It cannot safely be assumed to be zero or EUR 0.01/kWh.
- Fixed monthly electricity charge is contract-specific and is not verified
  by an official public price card in this research.
- Contract conditions include saldering rules changing on 1 January 2027;
  a single interval export-fee number does not reproduce all annual billing.

## Original blockers (see the 24 November update below)

1. Verify import fee **VAT inclusion** and effective date from a current
   official tariff sheet or Powerpeers confirmation.
2. Verify current fixed monthly electricity charge, VAT inclusion and
   contract applicability.
3. Verify export selling fee and how it is represented in EnerPrice's
   `purchase_fee_export` / `sell_market_price_includes_vat` semantics.
   If contract-specific or unavailable, design an explicit unsupported/
   customer-override state rather than using a fabricated zero.
4. Add matching entries to both bundled tariff registry and legacy profile
   fallback (the existing registry test expects identical supplier IDs).
5. Add regression tests for import fee, VAT, quarter-hour selection,
   export-price behavior and fallback.
6. Confirm CI, HACS and Hassfest before merging.

## Sources

- Official fee: https://powerpeers.nl/tarieven/lage-inkoopvergoeding/
- Official price explanation: https://powerpeers.nl/dynamische-energie/energieprijzen-dynamisch-energiecontract/
- Powerpeers product conditions (2025-10-01, secondary PDF mirror):
  https://www.gaslicht.com/userfiles/pdf/energie/powerpeers/pakketten/pakket-voorwaarden/PV251006-Powerpeers-dynamisch-stroom.pdf
- Issue: https://github.com/LenFaki/EnerPrice/issues/97

This supplier onboarding remains separate from Belgium support (#81)
and Daily Advisor GC/performance investigation (#96).

## Verification update — 24 November 2026

New primary sources narrow the blockers; the old export-fee unknown is resolved
for the published current example, but this is not yet a universal contract.

- Powerpeers explicitly publishes an export selling fee of EUR 0.01/kWh at
  https://powerpeers.nl/dynamische-energie/salderen-stopt-je-krijgt-meer-met-dynamisch/ .
- The official current contract PDF (version 1 January 2026), linked from the
  supplier's conditions page, states the selling fee includes VAT (section 10.2):
  https://powerpeers.eu/assets/website/uploads/Powerpeers-contractvoorwaarden_Dynamisch_Stroom.pdf .
- Import fee VAT can be independently checked against the public graph data.
  The official page labels the base graph price as including VAT. Its public
  endpoint is https://powerpeers.codecast.nl/services/public/market/prices/ELECTRICITY?fromDate=2026-11-24&toDate=2026-11-24 .
  One interval gives price 0.26140 and priceAllIn 0.38224 EUR/kWh. The difference
  0.12084, minus the official 2026 electricity tax 0.110848, is 0.009992 EUR/kWh,
  consistent with EUR 0.01 including VAT within the feed's five-decimal rounding.
  This is an inference from the supplier's published price components, not an
  independently stated VAT label attached to its marketing fee.
- Fixed charges conflict between two official pages: /voorwaarden/ gives
  EUR 0.221918/day for dynamic electricity (about EUR 6.75/month annually), while
  /energietarieven-voorwaarden/ gives EUR 0.205479/day (about EUR 6.25/month).
  Do not silently choose one universal current contract charge. The contract
  confirmation has priority according to section 2.2 of the official PDF.
- The 2026 terms also define different export rules before and after 1 January
  2027, including a per-quarter minimum formula from 2027 and a monthly floor.
  Existing EnerPrice linear export-fee calculations do not represent all those
  billing rules. A default profile needs an explicit supported scope, customer
  overrides, and tested date transitions before advertising full support.

Next deliverable: a contract-confirmed supplier setup or verified default tariff
with explicit export-model limits. The above research removes obsolete blockers
but is not a completed selectable supplier. Keep #97 open.
