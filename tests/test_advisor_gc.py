"""Regression tests for bounded advisor GC profiling."""

import gc
import logging
from unittest.mock import Mock

import pytest

from custom_components.nl_day_ahead_prices.advisor_gc import profile_advisor_gc


def test_gc_profile_records_collected_objects_and_unregisters(caplog):
    logger = logging.getLogger("test.enerprice.gc")
    callbacks_before = tuple(gc.callbacks)
    with caplog.at_level(logging.WARNING, logger=logger.name):
        with profile_advisor_gc("Daily Advisor", logger, threshold_ms=0):
            callback = gc.callbacks[-1]
            callback("start", {"generation": 2})
            callback("stop", {"generation": 2, "collected": 42, "uncollectable": 1})
    assert tuple(gc.callbacks) == callbacks_before
    assert "collected objects: (0, 0, 42)" in caplog.text
    assert "uncollectable objects: (0, 0, 1)" in caplog.text
    assert "EnerPrice Daily Advisor GC pressure:" in caplog.text
    assert "counters before/after" in caplog.text
    assert "cumulative collections delta" in caplog.text
    assert "peak RSS MiB before/after" in caplog.text
    assert "GC trigger context:" in caplog.text
    assert "net snapshots, not total allocations" in caplog.text


def test_gc_profile_always_unregisters_after_exception():
    callbacks_before = tuple(gc.callbacks)
    with pytest.raises(RuntimeError, match="failure"):
        with profile_advisor_gc("Smart Energy Advisor", Mock(), threshold_ms=999999):
            raise RuntimeError("failure")
    assert tuple(gc.callbacks) == callbacks_before


def test_gc_profile_does_not_warn_on_fast_calculation():
    logger = Mock()
    with profile_advisor_gc("Daily Advisor", logger, threshold_ms=999999):
        pass
    logger.warning.assert_not_called()


def test_gc_pressure_uses_bounded_counters_without_scanning_objects(monkeypatch):
    from custom_components.nl_day_ahead_prices import advisor_gc

    monkeypatch.setattr(gc, "get_objects", Mock(side_effect=AssertionError("full GC scan")))
    logger = Mock()
    with advisor_gc.profile_advisor_gc("Smart Energy Advisor", logger, threshold_ms=0):
        pass
    assert logger.warning.call_count == 3
    assert "GC trigger context" in logger.warning.call_args.args[0]


def test_peak_rss_is_optional_when_resource_is_unavailable(monkeypatch):
    from custom_components.nl_day_ahead_prices import advisor_gc

    monkeypatch.setattr(advisor_gc, "_peak_rss_mib", lambda: None)
    logger = Mock()
    with advisor_gc.profile_advisor_gc("Daily Advisor", logger, threshold_ms=0):
        pass
    assert logger.warning.call_count == 2
    assert "GC trigger context" in logger.warning.call_args.args[0]


def test_gen2_trigger_context_is_bounded_and_does_not_keep_frames(monkeypatch):
    from custom_components.nl_day_ahead_prices import advisor_gc

    logger = Mock()
    callbacks_before = tuple(gc.callbacks)
    with advisor_gc.profile_advisor_gc("Daily Advisor", logger, threshold_ms=0):
        callback = gc.callbacks[-1]
        for _ in range(5):
            callback("start", {"generation": 2})
            callback("stop", {"generation": 2, "collected": 0})
    assert tuple(gc.callbacks) == callbacks_before
    assert logger.warning.call_count == 3
    trigger_log = logger.warning.call_args
    assert "GC trigger context" in trigger_log.args[0]
    assert len(trigger_log.args[2]) == 3
    assert all(len(site) == 3 for site in trigger_log.args[2])


def test_allocated_blocks_can_be_unavailable(monkeypatch):
    from custom_components.nl_day_ahead_prices import advisor_gc

    monkeypatch.setattr(advisor_gc, "_allocated_blocks", lambda: None)
    logger = Mock()
    with advisor_gc.profile_advisor_gc("Daily Advisor", logger, threshold_ms=0):
        pass
    assert logger.warning.call_args.args[-2:] == (None, None)
