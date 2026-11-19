# Home Assistant worker bootstrap fix (v2.6.10-beta.13)

## Failure reproduced

Beta.12's `multiprocessing` target was a function in
`custom_components.nl_day_ahead_prices.advisor_process`. Home Assistant mounts
custom_components by temporarily inserting its configuration directory into
`sys.path`, importing the package and removing that directory again (see
`homeassistant.loader.async_mount_config_dir`).

A spawned interpreter receives the current sys.path, not the parent's loaded
modules. Unpickling the beta.12 target therefore fails with
`ModuleNotFoundError: No module named 'custom_components'` when the configuration
root is absent from the search path. The parent receives ConnectionResetError,
matching the user's beta.12 symptom. Earlier tests ran from the checkout and
missed HA's temporary-import-path behavior.

This has now been reproduced in a separate working directory under Python
3.14.10 with no PYTHONPATH. The beta.12 implementation failed; the fix returns
ready advice and reuses the same independent process. The parent's sys.path
remains unchanged.

## Fix

Spawn uses the always-importable standard-library `runpy.run_path` target and
an absolute `advisor_worker.py` path. The bootstrap adds the package root only
inside the new interpreter, imports the actual worker, and then receives model
objects. This avoids attempting to unpickle an EnerPrice target before the
package is importable. No HA search paths or process-wide GC policy are changed.

A bounded startup handshake confirms imports completed before the parent sends
the snapshot. Import failure is reported as a bootstrap error; startup timeout
terminates the child and closes the pipe. Existing request timeouts, cleanup,
60-second failure cooldown, tariff snapshots and obsolete-result checks remain.

## Verification

The regression test runs a separate driver outside the checkout, clears
PYTHONPATH, imports EnerPrice through a temporary path, removes it, and verifies
two calculations reuse a child PID while the parent search path is unchanged.
Startup error cleanup is covered as well. The full suite is checked on Python
3.12 and 3.14; CI now includes both versions. Existing process CPU/GC isolation,
financial parity, remote tariffs, shutdown and failure tests are retained.

This fixes a reproduced bootstrap defect. It does not establish that every
possible child-process exit (for example an OS kill) has the same cause, or
claim that all unrelated Home Assistant GC pauses are eliminated.
