"""File-based child bootstrap; independent of HA's temporary import mount."""

from __future__ import annotations

import sys
from contextlib import suppress
from pathlib import Path


def _bootstrap(connection):
    # This file runs only in the spawned interpreter. HA's sys.path is untouched.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    try:
        from custom_components.nl_day_ahead_prices.advisor_process import _process_main

        _process_main(connection)
    except Exception as error:  # noqa: BLE001 -- report child startup failure through the private pipe
        with suppress(OSError):
            connection.send({"ready": False, "error": type(error).__name__})
    finally:
        connection.close()


if __name__ == "__enerprice_worker__":
    _bootstrap(connection)  # noqa: F821 -- injected by runpy's init_globals, never user input
