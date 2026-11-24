# Powerpeers dynamic electricity (#97)

Implemented for the planned v2.7.0 release: select **Powerpeers (own contract)**
in EnerPrice options, then enter and confirm your contract settings. This
supplier uses customer-confirmed terms rather than a universal bundled tariff.

## Setup and calculation

1. Set the market to **NL**, currency EUR, and supplier **Powerpeers**.
2. Enter the monthly electricity charge **including VAT** from your contract.
   There is deliberately no default. An explicitly entered zero is allowed.
3. Check import and selling fees and their VAT checkboxes. EUR 0.01/kWh is a
   published suggestion; your confirmed contract takes precedence.
4. Explicitly choose whether the export market component before 2027 includes
   VAT. This is contract/customer dependent; no automatic choice is made.
5. Confirm that you checked all values. Missing, negative or non-finite amounts
   are rejected. Changing a fee reloads cached calculations.

Import: `market * (1 + VAT) + energy_tax_including_VAT + import_fee_including_VAT`.
The monthly fee is shown separately, not spread over unknown consumption.
`auto` resolution selects quarter-hour prices; an explicit hourly override is
retained. Hourly source data can be expanded, with conversion metadata exposed.

Export interval estimates follow the official 2026 dynamic product conditions:

- Before 2027: market component with the customer's selected VAT treatment,
  minus the confirmed selling fee including VAT.
- 2027–2029: the higher of EPEX excluding VAT and half of (EPEX + import fee),
  both excluding VAT, minus the selling fee including VAT.
- From 2030: EPEX excluding VAT minus the selling fee including VAT.

Local NL market dates determine the rule, including intervals around midnight.
The same confirmed profile and per-date rules survive the isolated Daily
Advisor process. Automatic registry updates never replace these manual terms.
Recheck the values when Powerpeers changes your contract.

## Limits

These are interval estimates, **not an annual or monthly bill**. Annual saldering
before 2027, the volume-weighted monthly gross reimbursement floor from 2027,
network costs and actual consumption are not reconstructed. Negative interval
values stay negative; applying a per-interval zero floor would be incorrect.
Potential differences in advice are not proven realized savings.

## Primary-source verification — 24 November 2026

- [Published import fee](https://powerpeers.nl/tarieven/lage-inkoopvergoeding/):
  EUR 0.01/kWh.
- [Published selling fee](https://powerpeers.nl/dynamische-energie/salderen-stopt-je-krijgt-meer-met-dynamisch/):
  EUR 0.01/kWh. It remains customer-confirmed rather than assumed universal.
- [Official dynamic product conditions, version 1 January 2026](https://powerpeers.eu/assets/website/uploads/Powerpeers-contractvoorwaarden_Dynamisch_Stroom.pdf):
  contract confirmation precedence (§2.2), selling fee including VAT (§10.2),
  dated export formula and monthly floor (§10.3).
- [Supplier price graph explanation](https://powerpeers.nl/dynamische-energie/energieprijzen-dynamisch-energiecontract/)
  labels the graph base price including VAT. Public graph data on 24 November
  had one interval at 0.26140 / 0.38224 EUR/kWh. The difference less 2026 tax
  0.110848 is 0.009992, consistent with a one-cent VAT-inclusive import fee
  within rounding. This VAT reading is an **inference**, so setup requires review.
- Published fixed charges differ: [conditions](https://powerpeers.nl/voorwaarden/)
  list EUR 0.221918/day (about EUR 6.75/month annually), while
  [tariffs and conditions](https://powerpeers.nl/energietarieven-voorwaarden/)
  list EUR 0.205479/day (about EUR 6.25/month). No universal monthly default is
  justified by those conflicting pages.

Coverage includes missing contract values, invalid numbers, separate contracts,
VAT choices, negative export prices, 2027/2030 boundaries, local midnight,
quarter-hour selection, settings reload and actual spawned-process parity.
