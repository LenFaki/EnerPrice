# Prepared Daily Advisor updates (v2.6.10-beta.11)

## Problem and change

Beta.10 field measurements showed 2–3 second Daily Advisor updates. Almost all
of those measured pauses were process-wide Python generation-2 garbage
collection, encountered during advice construction or export-price conversion.
The logs do not establish which integration created the collected objects.

This patch removes full daily-plan calculation and tariff conversion from
entity property reads. A single per-entry background task captures inputs on
the Home Assistant loop and runs the pure calculation in HA's executor. Both
daily sensors and the live Smart Energy Advisor share the prepared result.
Inputs are checked again before publication; superseded jobs are discarded.
Notifications wait for a fresh result. While a changed input is being processed,
daily advice is temporarily unavailable rather than publishing a stale plan.
The live heat-source comparison continues to update from current measurements.

Tariff conversions are reused across measurement changes. Forecast values,
timestamps, freshness, price changes, options, language and time buckets still
invalidate the advice. No sensor values are dropped from cache validation.
Unload cancels publication and clears retained snapshots.

Price sensors now share serialized sell-price arrays, cheapest-block results
and serialized chart periods per coordinator cycle. Previously every price
sensor repeated the same seven block searches and sell-price serialization.
The optimizer streams consecutive windows rather than retaining and ranking
all windows when no alternatives were requested. Chronological tie selection,
missing-interval checks and absolute-time DST handling are preserved.

## Validation

- Python test suite: 731 tests passed before final version bump; rerun on final commit.
- Ruff, supplier-registry validation and 18 Node audit lifecycle tests passed.
- New tests cover executor thread ownership, shared jobs, changing measurements,
  tariff reuse, price/options/time/language invalidation, obsolete-job rejection,
  unload, error retry suppression and equality with the existing financial model.
- Local comparison of 20 extended quarter-hour sensor attribute reads, sharing
  a coordinator cache: median 233.78 ms in beta.10 versus 34.18 ms in this patch
  (7 trials, same host; about 85% less). This synthetic result is not a Raspberry
  Pi measurement and does not measure a process-wide GC pause.
- In an independent one-hour daily-plan microbenchmark, calculation itself was
  not faster (1.788 versus 1.969 ms); no daily-algorithm speedup is claimed.

## Limits and acceptance

An executor thread is in the same Python process. A full GC can still hold the
GIL and pause other HA work. This patch does not claim to eliminate those
process-wide pauses or prove the original GC object ownership. GC diagnostics
remain active around the worker; warnings have not been disabled or raised to
a higher threshold. No global GC thresholds, collection policy or forced
collections are introduced.

After installing and restarting HA, compare new entries only. Existing logbook
counts remain historical. Confirm that prices, daily advice, solar scenarios
and notifications still update. Record any new slow entity updates and any
`Daily Advisor worker GC profile` warnings. Stable-release acceptance still
requires this field check; issue #96 must not be closed solely on CI results.
