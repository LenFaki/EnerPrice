# Daily Energy Advisor PR3

PR3 builds on merged PR1 and PR2. Existing advice, prices, entity IDs, services and
dashboard defaults are unchanged. This is development work; the manifest and
project version remain **2.5.4**. No automatic device control, email function,
paid API or new market-provider request is introduced.

## Notifications: explicit permission first

Daily Advisor remains opt-in. In its options:

1. Enable the notification preference. It defaults on for a new Daily Advisor
   configuration, but defaults off for existing PR1/PR2 configurations.
2. Select an available `notify.mobile_app_*` or `notify.persistent_notification`
   target. Broadcast aliases, groups and email services are deliberately excluded.
3. Check **I consent to delivery to the selected target** and save. This checkbox
   is unchecked each time the form opens: recheck it to retain permission when
   saving this form. Otherwise permission is revoked. Changing the target without
   renewed consent never transfers permission to another recipient.
4. Choose a local Home Assistant time (default **08:00**, whole minutes).

Preference alone never grants permission. Upgrading an existing installation
never sends unexpected messages. Disabling Daily Advisor or its notification
preference stops sends without deleting selections/history. Re-enabling a saved
preference retains previously granted consent for the same target; saving the
Daily Advisor form without the consent checkbox revokes that consent.

A briefing contains forecast solar production, today's grid-only best window and
at most three existing advice records. Missing information is explicitly unknown.
Solar scenarios retain their uncertainty and export-opportunity-cost caveat.
The message uses the configured advice language or HA's language (NL/EN).

The schedule follows **Home Assistant's timezone**, even outside the Netherlands.
Energy days and electricity windows remain **Europe/Amsterdam**, matching PR1/PR2.
Briefing windows display local HA time with UTC offsets; the energy date remains
the current Amsterdam market date.

### At-most-once attempts, not guaranteed delivery

- One-minute local checks, without polling any external price API.
- A maximum 30-minute catch-up window supports a short restart or temporarily
  absent target. No late afternoon replay of an old morning briefing.
- A nonexistent spring-DST time is skipped; a repeated autumn time gets at most
  one attempt. A monotonic local-date claim also prevents backward-clock replay.
- A claim is atomically saved, file-synced and directory-synced **before** calling
  HA's notify service. Reload waits for pending delivery/storage; consent and
  shutdown state are rechecked after saving.
- A timeout, crash after claiming or ambiguous failure is **not retried that day**.
  This deliberately favors missing one briefing over duplicate messages.
- `sent` means HA accepted the service call, not proof that a phone displayed it.
  `claimed` means a durable attempt exists but completion is uncertain.
- Missing/unavailable targets do not consume a claim. Corrupt or unwritable
  storage fails closed: no delivery until repaired and the integration reloaded.

See HA's [notification documentation](https://www.home-assistant.io/integrations/notify/)
and [Companion notification documentation](https://companion.home-assistant.io/docs/notifications/notifications-basic/).
No real notification is sent during the automated test suite.

## Energy evidence and limitations

Select separate cumulative import and export energy sensors (Wh/kWh,
`state_class: total_increasing`). The existing actual solar-energy input is reused
for solar production. These must be lifetime-style counters, **not daily-reset
utility meters**. Signed net power is not a substitute for two energy counters;
appliance consumption selections are not silently treated as whole-house meters.

Each Amsterdam calendar date stores:

- The first valid solar total forecast received before noon, with capture time.
- When available before noon, a separately captured full-day grid scenario using
  PR2's synchronized actual solar/grid power and existing forecast integration.
  It assumes constant household demand; it is not a trained consumption forecast.
  Incomplete power coverage, including missing night intervals, leaves predicted
  import/export unknown. A forecast arriving after noon is not retroactively
  presented as a prior prediction.
- Actual energy deltas only from fresh, monotonically increasing, same-source
  meter readings across both day boundaries. Boundary observations must be within
  90 seconds of midnight and their source reports no older than 90 seconds.
  Ordinary readings must be no older than 15 minutes; observation gaps over
  20 minutes invalidate the day's evidence.
- For each metric: predicted kWh, actual kWh, actual-minus-predicted error and
  comparable/incomplete quality. Unknown values remain null, never zero.

Counter freshness uses HA's `last_reported` timestamp (falling back to
`last_updated` on older/test states). A freshly reported unchanged counter can
therefore prove zero consumption; an unreported stale counter cannot. This proves
report freshness in HA, not an independent hardware audit. Forecast/power freshness
semantics remain unchanged. See [HA's timestamp documentation](https://developers.home-assistant.io/blog/2024/03/20/state_reported_timestamp/).

This strict policy deliberately rejects unreported stale sensors, reset counters,
missing boundaries and incomplete restarted days. A process restart/reload may
hide a meter reset; therefore any restored unfinalized day's meters are invalidated.
Finalized history remains available. The first reliable total normally requires
an uninterrupted complete calendar day. DST days correctly contain 23 or 25 hours.

### Potential is not realized

The potential financial difference is the maximum single morning advice estimate
for that day. Alternative/overlapping plans are never summed. This is the PR2
hypothetical comparison with its existing reference, not a measured saving.

**Realized savings remain null / not established.** Aggregate meter differences
cannot prove appliance execution, causation or an avoided-cost counterfactual.
PR3 does not invent that proof, and never multiplies a forecast error by a tariff
and calls it savings. Positive energy forecast accuracy is not proof of savings.
Negative prices and signed potential impacts retain the existing PR2 semantics.

## Bounded storage and dashboard

`daily_retention_days`: **1-90 completed dates**, default **30**, plus the current
date. Lowering retention prunes the in-memory and stored records on the next
minute tick. The notification-date watermark is independent of retention.

Private file: `.storage/nl_day_ahead_prices_daily_advisor_<entry_id>.json`.
There is at most one replaceable `.tmp` staging file per entry. JSON schema is
version 1, a fixed set of fields, a hard 512 KiB read/write limit and no raw interval
arrays. File work runs in HA's executor; writes use atomic replace with observable
errors. Malformed JSON is **not** renamed/deleted and treated as a fresh installation.
Future schema versions fail closed rather than being overwritten.

History is checkpointed every 15 minutes, at day rollover, before notification
attempts, after delivery, and on unload. Restored open-day counters are invalidated,
so an unsaved invalid sample cannot become successful evidence after a crash.

New stable unique IDs on the existing device:

- `<entry_id>_daily_notification_status`: gate/delivery state, claim date, time,
  timezone, storage status and retention metadata. No recipient IDs in attributes.
- `<entry_id>_daily_energy_performance`: latest completed date's three metrics,
  potential impact and separate unknown realized savings. No history array.

Generate the existing dashboard with `include_daily_advisor: true`. It now adds
notification/performance tiles and a predicted-versus-actual table using resolved
entity IDs. `use_apexcharts: false` still gives a built-in-card-only dashboard.
Existing dashboards are not modified automatically.

## Upgrade

1. Back up Home Assistant, including its `.storage` directory.
2. Install the PR3 branch over the PR2 build and restart HA.
3. Existing options and entities remain valid. No config-entry migration/version
   bump is required; missing PR3 settings have safe defaults and no consent.
4. Open Daily Advisor options to select cumulative energy counters. Enable and
   explicitly authorize notifications only when desired.
5. Regenerate the optional dashboard section to display the new status/table.
6. Allow a complete uninterrupted energy day; first-day unknowns are expected.

## Rollback And Recovery

Disable notifications first, restore the previous main/PR2 code, then restart HA.
Older code ignores PR3 options and does not read the PR3 history file. Keep that
file intact: reinstalling PR3 preserves claims and cannot replay the same date.
Optional stale entity-registry entries can be removed manually after rollback.

Do not delete/corrupt/restore an older claim file while notifications are enabled:
destroying the watermark destroys replay protection. If storage fails, disable
notifications, back up the failed file and inspect HA logs. Restore a valid backup
or reset only while consent is revoked; wait until the next local day before
reauthorizing notifications. Restoring an entire older HA backup similarly cannot
prove that a newer notification was already sent.

Automated tests use HA boundary doubles plus real temporary-file atomic storage.
Live HA/phone delivery is not exercised. A live-installation smoke test is still
recommended for target discovery, options selectors and generated dashboard cards.

## Nederlands

### Instellen

Meldingen vereisen drie zaken: voorkeur aan, een beschikbaar Companion-app- of
HA-notificatiedoel en expliciete toestemming. Bij nieuwe Daily Advisor-instellingen
staat de voorkeur aan; bij bestaande instellingen blijft deze uit. Alleen de
voorkeur aanzetten verzendt niets. Vink toestemming opnieuw aan bij het opslaan
van het formulier; zonder vinkje wordt toestemming ingetrokken.

De verzendtijd gebruikt de HA-tijdzone (standaard 08:00). De briefing toont
zonneprognose, beste netstroomvenster en maximaal drie adviezen. Geen apparaatbediening
of e-mail. Na een mislukte of onzekere verzendpoging volgt die dag geen herhaling.
Bij zomertijd wordt een niet-bestaande tijd overgeslagen; wintertijd verdubbelt niets.

### Energiecontrole

Kies cumulatieve, niet dagelijks resetbare meters voor zonneopwek, netafname en
teruglevering. Daggrenzen moeten vers gemeten zijn. Ontbrekende of verouderde
gegevens, tellerresets en onderbroken dagen blijven onbekend. Een herstart of
herlading maakt de nog niet afgesloten meetdag onvolledig; eerdere afgesloten
dagen blijven bewaard. Energiedagen volgen Amsterdam, ook als HA elders staat.

Netprognoses zijn alleen beschikbaar met een volledig gedekt zonprofiel en een
onderbouwde momentopname van huishoudelijk vermogen. Het blijft een scenario met
gelijkblijvend verbruik. Potentieel financieel verschil is geen bewezen besparing.
Gerealiseerde besparing blijft onbekend zonder aantoonbare uitvoering en geldige
referentie. Prognoseverschillen worden nooit automatisch als besparing geboekt.

### Bewaren, Upgraden En Terugzetten

Bewaar 1-90 afgesloten dagen (standaard 30), plus vandaag. Sensorattributen tonen
alleen de laatste afgesloten dag; de verzendmarkering blijft apart bewaard.
Maak voor installatie een HA-back-up, installeer PR3 en herstart. Bestaande
configuraties blijven werken en versturen niet onverwacht meldingen. Selecteer
meters, geef desgewenst toestemming en genereer de optionele dashboardsectie opnieuw.

Voor rollback: zet meldingen uit, zet PR2-code terug en herstart. Bewaar het nieuwe
opslagbestand en de verzendmarkering. Wis of herstel geen oude opslag terwijl
meldingen aanstaan. Bij opslagfouten: trek toestemming in, maak een back-up van
het bestand, herstel de opslag en autoriseer pas de volgende lokale dag opnieuw.
Een live HA-test blijft aanbevolen; de tests versturen geen echte meldingen.
