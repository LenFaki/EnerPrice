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
