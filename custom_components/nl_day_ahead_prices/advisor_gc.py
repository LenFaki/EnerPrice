"""Bounded, opt-in GC pause diagnostics for advisor calculations.

The GC callback list is process-wide. Record only collections occurring on
the invoking thread and always remove the callback, including on exceptions.
No global GC settings are modified and no collections are forced.
"""

from __future__ import annotations

import gc
import logging
from contextlib import contextmanager
from threading import get_ident
from time import perf_counter, thread_time


@contextmanager
def profile_advisor_gc(label: str, logger: logging.Logger, threshold_ms: float = 250.0):
    """Log expensive calls with GC wall/CPU time and collected object counts."""
    owner = get_ident()
    started = perf_counter()
    started_cpu = thread_time()
    active = {}
    wall_ms = [0.0, 0.0, 0.0]
    cpu_ms = [0.0, 0.0, 0.0]
    events = [0, 0, 0]
    collected = [0, 0, 0]
    uncollectable = [0, 0, 0]

    def on_gc(phase, info):
        if get_ident() != owner:
            return
        generation = info.get("generation")
        if generation not in (0, 1, 2):
            return
        if phase == "start":
            active[generation] = (perf_counter(), thread_time())
        elif phase == "stop":
            times = active.pop(generation, None)
            if times is None:
                return
            wall_ms[generation] += (perf_counter() - times[0]) * 1000
            cpu_ms[generation] += (thread_time() - times[1]) * 1000
            events[generation] += 1
            collected[generation] += info.get("collected", 0)
            uncollectable[generation] += info.get("uncollectable", 0)

    gc.callbacks.append(on_gc)
    try:
        yield
    finally:
        gc.callbacks.remove(on_gc)
        elapsed = (perf_counter() - started) * 1000
        if elapsed >= threshold_ms:
            logger.warning(
                "EnerPrice %s GC profile: elapsed %.1f ms; thread CPU %.1f ms; "
                "GC wall ms by generation: %s; GC CPU ms by generation: %s; "
                "GC events: %s; collected objects: %s; uncollectable objects: %s.",
                label,
                elapsed,
                (thread_time() - started_cpu) * 1000,
                tuple(round(value, 1) for value in wall_ms),
                tuple(round(value, 1) for value in cpu_ms),
                tuple(events),
                tuple(collected),
                tuple(uncollectable),
            )
