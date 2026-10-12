"""Confirmed atomic persistence for at-most-once notification claims.

Unlike HA Store, write errors must propagate and malformed input must never be
quarantined as a missing file. All filesystem work runs in HA's executor.
"""

import json
import os
from pathlib import Path

MAX_BYTES = 512 * 1024


def _invalid_constant(value):
    raise ValueError(f"Invalid JSON number: {value}")


def _load(path):
    try:
        with open(path, "rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(raw) > MAX_BYTES:
        raise ValueError("Daily Advisor history exceeds size limit")
    value = json.loads(raw, parse_constant=_invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("Daily Advisor history must be an object")
    return value


def _save(path, value):
    raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError("Daily Advisor history exceeds size limit")
    directory = str(Path(path).parent)
    os.makedirs(directory, exist_ok=True)
    # One replaceable staging file per entry also bounds leftovers after a crash.
    temporary = f"{path}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class DailyAdvisorStore:
    """Single bounded per-entry JSON document with observable durability failures."""

    def __init__(self, hass, key):
        self.hass = hass
        self.path = hass.config.path(".storage", key)

    async def async_load(self):
        return await self.hass.async_add_executor_job(_load, self.path)

    async def async_save(self, value):
        await self.hass.async_add_executor_job(_save, self.path, value)
