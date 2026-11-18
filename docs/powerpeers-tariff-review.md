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

## Before adding to supplier_tariffs.json and supplier_profiles.json

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
