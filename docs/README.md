# EnerPrice documentation / Documentatie

Current branch documentation: **v2.7.1**. The [GitHub releases page](https://github.com/LenFaki/EnerPrice/releases)
shows the published version; preparing documentation in a PR does not publish a release.

| Subject / Onderwerp | Documentation / Documentatie |
| --- | --- |
| Setup, daily advice and troubleshooting | [English user guide](enerprice-v2-6-user-guide.md) · [Nederlandse handleiding](enerprice-v2-6-gebruikershandleiding.md) |
| Feature overview / Functieoverzicht | [English README](../README.md) · [Nederlandse README](../README.nl.md) |
| Powerpeers contract settings | [Confirmed tariffs, VAT, dated export rules and limits](powerpeers-tariff-review.md) |
| Belgian market / Belgische markt | [Wholesale-only setup, available services and exclusions](belgium-support-plan.md) |
| Dashboard examples / Dashboardvoorbeelden | [English recipes](dashboard-recipes.md) · [Nederlandse voorbeelden](dashboard-recipes.nl.md) |
| NL supplier tariffs | [Registry, provenance and troubleshooting](supplier-tariff-registry.md) |
| v2.7.1 maintenance / Onderhoud | [Bilingual patch notes](releases/v2.7.1.md) |
| v2.7.0 changes / Wijzigingen | [Bilingual release notes](releases/v2.7.0.md) · [Changelog](../CHANGELOG.md) |
| Release procedure | [Maintainer instructions](releases.md) |

NL supports configured supplier all-in estimates and optional Smart/Daily financial advice.
BE supports wholesale prices and consumption planning only, excluding VAT, taxes,
grid tariffs and supplier fees. Powerpeers requires customer-confirmed contract values.

**Nederlands:** NL ondersteunt ingestelde all-inramingen en optioneel financieel
Smart/Dagelijks advies. BE ondersteunt alleen marktprijzen en verbruiksplanning,
exclusief btw, belastingen, nettarieven en leverancierskosten. Powerpeers gebruikt
de contractwaarden die je zelf controleert en bevestigt.

The existing user-guide filenames and screenshot anchors are retained so old links
keep working. Dashboard screenshots are dated NL examples, not a default BE dashboard.

Historical material: `daily-advisor*.md` records the PR1–PR3 design stages;
`performance-beta*.md` records the performance investigation and worker bootstrap.
Those stage-specific version statements are not current installation instructions.
Historical release notes under `releases/` describe each release as published.
