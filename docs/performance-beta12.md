# Isolated Daily Advisor process (v2.6.10-beta.12)

> Historical engineering record. Version/status statements below describe that development stage. For current v2.7 setup, see the [user guide](enerprice-v2-6-user-guide.md) or [Nederlandse handleiding](enerprice-v2-6-gebruikershandleiding.md). The isolated runtime was released in [v2.6.10](releases/v2.6.10.md) and is retained in v2.7; financial Daily Advisor remains NL-only.

## Why beta.11 was insufficient

Field logs at 10:00:39 show a worker-thread full GC lasting 3217.1 ms. The live
Smart Energy Advisor simultaneously spent 3207.1 ms elapsed but only 47.2 ms of
thread CPU time, and its own GC callback observed no collection. These figures
are consistent with the worker holding the same interpreter GIL during GC.
A thread moved the trigger but did not isolate HA from the pause.

## Change

The daily calculation now runs in one persistent Python process per enabled
entry, created with the explicit `spawn` context. It has its own interpreter,
GIL and GC heap, and does not fork/copy HA's object graph. The existing async
runtime still coalesces input changes, rejects obsolete results and shares the
prepared plan. HA's executor only waits for private local pipe I/O; it never
falls back to doing the calculation in the HA process.

Only prices, per-date supplier tariffs, validated forecasts and the two needed
calculation options are sent. Config entries, HA objects, API tokens and other
unrelated options are not transferred. Per-date tariffs are resolved in HA
before transfer so an active remote registry and future tariff changes cannot
silently revert to the child's bundled registry. Price conversions remain
cached inside the child and are not returned to HA.

The child is reused. Response waiting times out after 15 seconds, then the child
is terminated and joined, with kill escalation. Unload also terminates an
in-flight job. Failed starts/calculations have a 60-second cooldown across all
measurement changes; there is no process restart storm. Child GC diagnostics,
if slow, are forwarded with the child PID. No global HA GC policy is changed.

## Validation

- 739 Python tests pass, including real spawned-process and existing regressions.
- A real child test builds and collects 40,000 cycles and runs CPU-bound code
  for at least 0.5 seconds of thread CPU time while the parent asyncio loop
  continues ticking. The child PID differs from the parent's.
- Real-process tests verify financial-output parity, PID reuse, per-date remote
  tariffs, child crashes, calculation errors, timeout cleanup and interruption
  of an in-flight calculation during close.
- Scheduling tests retain freshness/obsolete-job rejection, one shared job,
  unchanged financial semantics and failure-backoff coverage.

## Acceptance and scope

This isolates the specific daily-worker GC pause demonstrated in beta.11.
Home Assistant still has its own GC, and IPC/result publication can allocate in
HA. This is not a claim to fix the entire HA installation's memory behavior or
to prove which integration created its collected objects. It adds one small
Python process per enabled Daily Advisor entry; field resource use still needs
checking on the user's Raspberry Pi.

After restart, inspect new events only: confirm current prices, live heat
advice, daily plans and notifications update. Any new entity warning still
needs its timestamp and accompanying profiles. Do not close #96 solely because
CI passes, or suppress GC logging to make acceptance appear successful.
