"""Spawned advisor process: independent interpreter, GIL and GC object graph.

The private local pipe carries trusted snapshots only, never Home Assistant
objects. Spawn deliberately avoids inheriting HA's heap via fork. All pipe and
process operations run in HA's I/O executor, not on its event loop.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
from contextlib import suppress

_LOGGER = logging.getLogger(__name__)


class _DiagnosticHandler(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        if len(self.messages) < 8:
            self.messages.append(record.getMessage())


def _process_main(connection):
    """Persistent child; conversion cache never crosses back into HA."""
    from .daily_runtime import calculate_snapshot

    logger = logging.getLogger("custom_components.nl_day_ahead_prices.daily_runtime")
    handler = _DiagnosticHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    converted = None
    try:
        while True:
            try:
                snapshot = connection.recv()
            except EOFError:
                return
            if snapshot is None:
                return
            handler.messages.clear()
            try:
                result, converted = calculate_snapshot(snapshot, converted)
                response = {"ok": True, "result": result, "pid": os.getpid(), "diagnostics": handler.messages}
            except Exception as err:  # noqa: BLE001 -- isolate a failed calculation, not the HA process
                response = {"ok": False, "error": type(err).__name__}
            connection.send(response)
    finally:
        connection.close()
        logger.removeHandler(handler)


class AdvisorProcess:
    """One bounded request at a time; caller owns serialization of jobs."""

    def __init__(self, *, timeout=15.0, worker_target=_process_main):
        self.timeout = timeout
        self._target = worker_target
        self._process = None
        self._connection = None
        self._closed = False

    def _start(self):
        if self._closed:
            raise RuntimeError("Daily Advisor process is closed")
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=self._target, args=(child,), name="EnerPrice Daily Advisor", daemon=True)
        try:
            process.start()
        except BaseException:
            parent.close()
            child.close()
            raise
        child.close()
        self._process, self._connection = process, parent
        # close() can race a spawn already running in another executor thread.
        if self._closed:
            self._dispose()
            raise RuntimeError("Daily Advisor process is closed")

    def calculate(self, snapshot):
        """Blocking IPC only; calculation executes in the separate process."""
        if self._closed:
            raise RuntimeError("Daily Advisor process is closed")
        if self._process is None:
            self._start()
        connection = self._connection
        try:
            connection.send(snapshot)
            if not connection.poll(self.timeout):
                raise TimeoutError("Daily Advisor process response timed out")
            response = connection.recv()
            if not isinstance(response, dict) or not response.get("ok"):
                raise RuntimeError("Daily Advisor process calculation failed")
            for diagnostic in response.get("diagnostics", ()):
                _LOGGER.warning("EnerPrice isolated process %s: %s", response["pid"], diagnostic)
            return response["result"]
        except BaseException:
            self._dispose()
            raise

    def _dispose(self):
        process, connection = self._process, self._connection
        self._process = self._connection = None
        if process is not None:
            with suppress(OSError, ValueError):
                if process.is_alive():
                    process.terminate()
                process.join(timeout=2.0)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=2.0)
                if not process.is_alive():
                    process.close()
        if connection is not None:
            with suppress(OSError):
                connection.close()

    def close(self):
        """Terminate a stalled calculation too; never wait indefinitely on exit."""
        self._closed = True
        self._dispose()
