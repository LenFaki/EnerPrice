"""Pure-Python registry validation shared by runtime and maintainer tooling."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import date, datetime, timedelta
from math import isfinite
from typing import Any
from urllib.parse import urlparse

from .supplier_registry import SupplierRegistry, parse_registry

MAX_KWH_FEE = 5
MAX_MONTHLY_FEE = 1000
NUMERIC_TARIFF_BOUNDS = {
    "purchase_fee_import": (0, MAX_KWH_FEE),
    "purchase_fee_export": (-MAX_KWH_FEE, MAX_KWH_FEE),
    "fixed_monthly_fee_electricity": (0, MAX_MONTHLY_FEE),
    "feed_in_fee": (-MAX_KWH_FEE, MAX_KWH_FEE),
    "imbalance_fee": (-MAX_KWH_FEE, MAX_KWH_FEE),
}
REQUIRED_SUPPLIERS = frozenset({
    "zonneplan", "tibber", "anwb_energie", "easy_energy", "eneco", "vandebron",
    "vattenfall", "greenchoice", "energyzero", "samsam", "pure_energie",
})
TARIFF_FIELDS = frozenset({
    "valid_from", "valid_until", "purchase_fee_import", "purchase_fee_export",
    "fixed_monthly_fee_electricity", "vat_included", "export_vat_included",
    "settlement_resolution", "source_type", "source_url", "last_verified", "notes",
    "feed_in_fee", "imbalance_fee", "profile_version", "supports_hourly_prices",
    "supports_quarter_hour_prices",
})

def utc_timestamp(value: Any) -> datetime:
    """Require an ISO timestamp with an explicit UTC offset."""
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("Invalid UTC timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("Timestamp must be UTC")
    return parsed


def parse_remote_registry(payload: Any) -> SupplierRegistry:
    """Validate the entire remote candidate before using the shared adapter."""
    if not isinstance(payload, dict):
        raise ValueError("Registry must be an object")
    required = {"schema_version", "revision", "country", "currency", "suppliers"}
    if not required <= payload.keys() or payload.keys() - required - {"published_at"}:
        raise ValueError("Invalid registry fields")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("Unsupported schema_version")
    if type(payload["revision"]) is not int or payload["revision"] < 1:
        raise ValueError("revision must be a positive integer")
    if "published_at" in payload:
        utc_timestamp(payload["published_at"])
    suppliers = payload["suppliers"]
    if not isinstance(suppliers, dict) or not REQUIRED_SUPPLIERS <= suppliers.keys() or len(suppliers) > 100:
        raise ValueError("Missing required supplier or invalid supplier map")
    for key, supplier in suppliers.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
            raise ValueError("Invalid supplier ID")
        if not isinstance(supplier, dict) or set(supplier) != {"name", "tariffs"}:
            raise ValueError("Invalid supplier fields")
        if not isinstance(supplier["name"], str) or not 1 <= len(supplier["name"]) <= 200:
            raise ValueError("Invalid supplier name")
        records = supplier["tariffs"]
        if not isinstance(records, list) or not 1 <= len(records) <= 100:
            raise ValueError("Invalid tariff list")
        for record in records:
            if not isinstance(record, dict) or set(record) != TARIFF_FIELDS:
                raise ValueError("Invalid tariff fields")
            for field, (minimum, maximum) in NUMERIC_TARIFF_BOUNDS.items():
                value = record[field]
                if field == "imbalance_fee" and value is None:
                    continue
                if type(value) not in (int, float):
                    raise ValueError(f"{field} must be numeric, not boolean or null")
                # Integers are finite; avoid float conversion of enormous JSON integers.
                if isinstance(value, float) and not isfinite(value):
                    raise ValueError(f"{field} must be finite")
                if not minimum <= value <= maximum:
                    raise ValueError(f"{field} outside safety bounds")
            for field in ("valid_from", "valid_until", "last_verified"):
                value = record[field]
                if value is not None:
                    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        raise ValueError("Invalid ISO date")
                    date.fromisoformat(value)
            url = record["source_url"]
            if record["source_type"] in ("official", "secondary") and url is None:
                raise ValueError("Sourced tariff requires a URL")
            if url is not None:
                if not isinstance(url, str) or len(url) > 2048 or any(c.isspace() for c in url):
                    raise ValueError("Invalid source URL")
                parsed_url = urlparse(url)
                if (parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname
                        or parsed_url.username or parsed_url.password):
                    raise ValueError("Invalid public source URL")
                _ = parsed_url.port  # Validate malformed/out-of-range ports too.
            if record["notes"] is not None and (
                not isinstance(record["notes"], str) or len(record["notes"]) > 10000
            ):
                raise ValueError("Invalid notes")
            if type(record["profile_version"]) is not int or record["profile_version"] != 2:
                raise ValueError("Invalid profile version")
    # Shared validation rejects wrong types, non-finite amounts and unsupported enums.
    parsed = parse_registry({
        "registry_version": payload["schema_version"], "country": payload["country"],
        "currency": payload["currency"], "suppliers": suppliers,
    })
    for profiles in parsed.suppliers.values():
        validate_periods(profiles)
    return SupplierRegistry(
        parsed.version,
        {key: tuple(replace(p, registry_source="remote", registry_revision=payload["revision"]) for p in profiles)
         for key, profiles in parsed.suppliers.items()},
        payload["revision"], payload.get("published_at"),
    )


def validate_periods(profiles) -> None:
    """Reject inclusive overlaps, including unbounded/duplicate periods."""
    ordered = sorted(profiles, key=lambda p: p.valid_from or "")
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if previous.valid_until is None or current.valid_from is None or current.valid_from <= previous.valid_until:
            raise ValueError("Overlapping tariff periods")


def decode_registry(raw: bytes) -> dict:
    """Reject duplicate JSON keys as well as invalid/non-finite JSON."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Non-finite JSON number")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
