# Belgium support — phased implementation (issue #81)

> **Not supported for end users yet.** This branch only introduces explicit
> ENTSO-E bidding-zone selection for NL (`10YNL----------L`) and BE
> (`10YBE----------2`). It does **not** enable correct Belgian all-in prices.

## Phase 1 — wholesale market prices (required before enabling BE)

- Validate the Nord Pool and Energy-Charts BE day-ahead endpoints with fixtures.
- Validate ENTSO-E BE prices with hourly/quarter-hour intervals, missing tomorrow
  prices and Europe/Brussels DST transitions (23/25-hour days).
- Expose an explicit, validated NL/BE market selector; do not accept arbitrary
  country strings or silently fall back to Dutch market data.
- Ensure that market-only mode does **not** add Dutch energy tax (currently
  `DEFAULT_ENERGY_TAX`), Dutch VAT assumptions or Dutch supplier fees.
- Preserve existing NL behavior and add NL/BE regression tests.

## Phase 2 — Belgian contract-specific pricing

- Separate Belgium from the NL supplier tariff registry (currently `country: NL`).
- Add suppliers/contracts only after checking current official tariff cards.
  Supplier names alone do not establish a universal markup or export rate.
- Model supplier formulas (including index multiplier), green-certificate
  contributions, network charges, regional levies, VAT and injection separately.
- Account for differences between Flanders, Wallonia and Brussels; label
  unsupported regions/contracts clearly.
- Keep scheduling based on wholesale price available even if all-in price
  calculation is not supported.

## Release policy

This is a draft foundation, not a complete Belgian market implementation.
Do not merge it as user-visible Belgium support until phase 1 is complete and
tested. Keep it independent of issue #96 performance work and issue #97
Powerpeers (NL) supplier addition.

References:
- https://github.com/LenFaki/EnerPrice/issues/81
- https://www.creg.be/nl/contract-met-dynamische-elektriciteitsprijs

## Delivery status — 24 November 2026

The stable performance release v2.6.10 is published. This draft has been
refreshed against that stable main so it contains only the three Belgium
foundation files and does not undo shared-advisor fixes from later beta work.
Provider, fallback and price-resolution tests pass with the new EIC mapping.

Next user-facing deliverable is phase 1: explicit BE wholesale-only mode,
provider fixtures and validation, with no Dutch taxes or supplier assumptions.
Contract-specific Belgian all-in prices remain phase 2 and must not be inferred
from a country selector alone. This foundation is not a finished feature and
#81 remains open until its stated acceptance criteria are met.
