"""Regression tests for non-blocking Smart Energy Advisor updates."""

import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SOURCE = Path(__file__).parents[1] / "custom_components/nl_day_ahead_prices/sensor.py"
tree = ast.parse(SOURCE.read_text())
sensor_node = next(
    node for node in tree.body
    if isinstance(node, ast.ClassDef) and node.name == "NLSmartEnergyAdvisorSensor"
)
methods = [
    node for node in sensor_node.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name in {"_request_update", "_calculate_async", "_cancel_pending", "_handle_coordinator_update"}
]
for node in methods:
    node.decorator_list = []
namespace = {
    "asyncio": asyncio,
    "_LOGGER": logging.getLogger(__name__),
}
exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
    name="AdvisorWorker", bases=[], keywords=[], body=methods, decorator_list=[]
)], type_ignores=[])), str(SOURCE), "exec"), namespace)
AdvisorWorker = namespace["AdvisorWorker"]


def make_worker():
    worker = AdvisorWorker()
    worker._revision = 0
    worker._task = None
    worker._result = None
    worker.hass = SimpleNamespace(async_create_task=asyncio.create_task)
    worker.async_write_ha_state = Mock()
    return worker


@pytest.mark.asyncio
async def test_advice_is_calculated_in_executor_not_state_callback():
    worker = make_worker()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def executor(fn):
        entered.set()
        await release.wait()
        return fn()

    worker.hass.async_add_executor_job = executor
    worker._build_calculation = lambda: lambda: {"state": "cheap_grid"}
    worker._request_update()
    await asyncio.wait_for(entered.wait(), 3)
    assert worker._result is None
    worker.async_write_ha_state.assert_not_called()
    release.set()
    await asyncio.wait_for(worker._task, 3)
    assert worker._result == {"state": "cheap_grid"}
    worker.async_write_ha_state.assert_called_once_with()


@pytest.mark.asyncio
async def test_obsolete_result_is_discarded_and_newest_published():
    worker = make_worker()
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def executor(fn):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
            return {"state": "old"}
        return {"state": "new"}

    worker.hass.async_add_executor_job = executor
    worker._build_calculation = lambda: lambda: {}
    worker._request_update()
    await asyncio.wait_for(entered.wait(), 3)
    worker._request_update()
    release.set()
    await asyncio.wait_for(worker._task, 3)
    assert calls == 2
    assert worker._result == {"state": "new"}
    worker.async_write_ha_state.assert_called_once_with()


@pytest.mark.asyncio
async def test_unload_cancels_pending_worker():
    worker = make_worker()
    entered = asyncio.Event()

    async def executor(fn):
        entered.set()
        await asyncio.Event().wait()

    worker.hass.async_add_executor_job = executor
    worker._build_calculation = lambda: lambda: {}
    worker._request_update()
    await asyncio.wait_for(entered.wait(), 3)
    task = worker._task
    worker._cancel_pending()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert worker._result is None
    worker.async_write_ha_state.assert_not_called()
