"""Offline tariff selection and compatibility adapter.

Load once in HA's executor before entities are created. Date selection is never
cached, so midnight changes do not need a restart. Validated remote snapshots
overlay bundled data; custom values win and legacy profiles remain the final
fallback. Transport, persistence and strict remote validation live separately.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import date, datetime
from functools import lru_cache
from math import isfinite
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .const import CONF_SUPPLIER_TARIFF_UPDATES, SUPPLIER_UPDATES_AUTOMATIC, SUPPLIER_UPDATES_BUNDLED
from .price_resolution import get_supplier_price_resolution
from .supplier_profiles import SupplierProfile, load_supplier_profiles, normalize_supplier_profile

REGISTRY_FILE = Path(__file__).with_name("supplier_tariffs.json")
MARKET_TIMEZONE = ZoneInfo("Europe/Amsterdam")
SUPPLIER_TARIFF_VERIFY_WARNING_DAYS = 60
SUPPLIER_TARIFF_STALE_DAYS = 120
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SupplierRegistry:
    version: int
    suppliers: dict[str, tuple[SupplierProfile, ...]]
    revision: int = 1
    published_at: str | None = None


_remote_registry: SupplierRegistry | None = None


def supplier_update_mode(options: dict) -> str:
    """Missing options retain Automatic behaviour without a migration."""
    return (SUPPLIER_UPDATES_BUNDLED if options.get(CONF_SUPPLIER_TARIFF_UPDATES) == SUPPLIER_UPDATES_BUNDLED
            else SUPPLIER_UPDATES_AUTOMATIC)


def activate_remote_registry(candidate: SupplierRegistry | None) -> None:
    """Swap a fully validated snapshot on the HA event loop, without yielding."""
    global _remote_registry
    _remote_registry = candidate


def market_date(value: date | datetime | None = None) -> date:
    if value is None:
        return datetime.now(MARKET_TIMEZONE).date()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Tariff selection requires a timezone-aware datetime")
        return value.astimezone(MARKET_TIMEZONE).date()
    return value


def _date(value: Any) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Date must be an ISO date or null")
    return date.fromisoformat(value)


def parse_registry(payload: Any) -> SupplierRegistry:
    """Reject invalid registries as a unit so fallback remains predictable."""
    if (
        not isinstance(payload, dict)
        or type(payload.get("registry_version")) is not int
        or payload["registry_version"] != 1
    ):
        raise ValueError("Unsupported supplier registry version")
    if payload.get("country") != "NL" or payload.get("currency") != "EUR":
        raise ValueError("Registry must use NL and EUR")
    revision = payload.get("revision", 1)
    if type(revision) is not int or revision < 1:
        raise ValueError("Invalid bundled registry revision")
    suppliers = payload.get("suppliers")
    if not isinstance(suppliers, dict):
        raise ValueError("suppliers must be an object")
    parsed = {}
    required = {
        "valid_from",
        "valid_until",
        "purchase_fee_import",
        "purchase_fee_export",
        "fixed_monthly_fee_electricity",
        "vat_included",
        "settlement_resolution",
        "source_type",
        "source_url",
        "last_verified",
        "notes",
    }
    for key, supplier in suppliers.items():
        if not isinstance(supplier, dict) or not isinstance(supplier.get("name"), str):
            raise ValueError("Supplier must have a name")
        records = supplier.get("tariffs")
        if not isinstance(records, list) or not records:
            raise ValueError("Supplier must have tariff records")
        profiles = []
        for record in records:
            if not isinstance(record, dict) or not required.issubset(record):
                raise ValueError("Missing tariff fields")
            start, end = _date(record["valid_from"]), _date(record["valid_until"])
            if start and end and end < start:
                raise ValueError("Tariff end precedes start")
            if record["settlement_resolution"] not in {"hourly", "quarter_hour"}:
                raise ValueError("Invalid settlement resolution")
            if record["source_type"] not in {"official", "secondary", "manual", "unknown"}:
                raise ValueError("Invalid source type")
            url = record["source_url"]
            if url is not None and (
                not isinstance(url, str) or urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc
            ):
                raise ValueError("Invalid source URL")
            if record["notes"] is not None and not isinstance(record["notes"], str):
                raise ValueError("notes must be a string or null")
            if record.get("profile_version", 2) != 2:
                raise ValueError("Unsupported profile version")
            for field in (
                "vat_included",
                "export_vat_included",
                "supports_hourly_prices",
                "supports_quarter_hour_prices",
            ):
                if field in record and type(record[field]) is not bool:
                    raise ValueError(f"{field} must be boolean")
            for field in (
                "purchase_fee_import",
                "purchase_fee_export",
                "fixed_monthly_fee_electricity",
                "feed_in_fee",
                "imbalance_fee",
            ):
                value = record.get(field, 0)
                if value is None and field == "imbalance_fee":
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                    raise ValueError(f"Invalid amount: {field}")
            profile = normalize_supplier_profile(
                {
                    **record,
                    "name": supplier["name"],
                    "price_resolution": record["settlement_resolution"],
                    "purchase_fee_includes_vat": record["vat_included"],
                    "sell_fee_includes_vat": record.get("export_vat_included", record["vat_included"]),
                },
                key,
            )
            profiles.append(
                replace(
                    profile,
                    valid_from=record["valid_from"],
                    valid_until=record["valid_until"],
                    source_type=record["source_type"],
                    registry_version=1,
                    registry_source="bundled",
                    registry_revision=revision,
                )
            )
        parsed[key] = tuple(profiles)
    return SupplierRegistry(1, parsed, revision)


@lru_cache(maxsize=1)
def load_registry() -> SupplierRegistry:
    """Perform disk I/O only during executor-backed startup/configuration."""
    try:
        return parse_registry(json.loads(REGISTRY_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, KeyError) as err:
        _LOGGER.warning("Supplier registry unavailable; using legacy profiles: %s", err)
        return SupplierRegistry(1, {}, revision=0)


def select_tariff(
    registry: SupplierRegistry, supplier_id: str, on: date | datetime | None = None
) -> SupplierProfile | None:
    today = market_date(on)
    candidates = [
        p
        for p in registry.suppliers.get(supplier_id, ())
        if (_date(p.valid_from) or date.min) <= today <= (_date(p.valid_until) or date.max)
    ]
    if len(candidates) > 1:
        _LOGGER.warning(
            "Overlapping tariff periods for %s on %s; using newest valid_from (last record breaks ties)",
            supplier_id,
            today,
        )
    return max(enumerate(candidates), key=lambda pair: (pair[1].valid_from or "", pair[0]))[1] if candidates else None


def get_supplier_profiles(
    on: date | datetime | None = None, *, mode: str = SUPPLIER_UPDATES_AUTOMATIC,
    remote_view: SupplierRegistry | None = None,
) -> dict[str, SupplierProfile]:
    """Keep supplier IDs and legacy fields while resolving today's registry."""
    profiles = dict(load_supplier_profiles())
    bundled = load_registry()
    remote = remote_view if remote_view is not None else _remote_registry
    registries = [bundled]
    if mode != SUPPLIER_UPDATES_BUNDLED and remote is not None and remote.revision > bundled.revision:
        registries.append(remote)
    for registry in registries:
        for key in registry.suppliers:
            selected = select_tariff(registry, key, on)
            if selected is not None and key != "custom":
                profiles[key] = selected
    return {key: replace(profile, tariff_registry_mode=mode) for key, profile in profiles.items()}


def get_supplier_tariff(
    supplier_id: str, on: date | datetime | None = None, *, custom: SupplierProfile | None = None,
    mode: str = SUPPLIER_UPDATES_AUTOMATIC,
) -> SupplierProfile | None:
    if custom is not None:
        return replace(custom, registry_source="custom", source_type="manual")
    return get_supplier_profiles(on, mode=mode).get(supplier_id)


def tariff_freshness(last_verified: str | None, on: date | datetime | None = None) -> tuple[str, int | None]:
    try:
        verified = _date(last_verified)
    except (ValueError, TypeError):
        verified = None
    if verified is None:
        return "unknown", None
    age = (market_date(on) - verified).days
    if age < 0:
        return "unknown", None
    if age > SUPPLIER_TARIFF_STALE_DAYS:
        return "stale", age
    if age > SUPPLIER_TARIFF_VERIFY_WARNING_DAYS:
        return "verification_recommended", age
    return "current", age


def tariff_metadata(profile: SupplierProfile, on: date | datetime | None = None) -> dict[str, Any]:
    today = market_date(on)
    status, age = tariff_freshness(profile.last_verified, today)
    custom = profile.key == "custom" or profile.registry_source == "custom"
    resolution = get_supplier_price_resolution(profile, datetime.combine(today, datetime.min.time(), MARKET_TIMEZONE))
    return {
        "supplier_tariff_status": "custom" if custom else status,
        "supplier_tariff_valid_from": profile.valid_from,
        "supplier_tariff_valid_until": profile.valid_until,
        "supplier_tariff_last_verified": profile.last_verified,
        "supplier_tariff_source_type": "manual" if custom else profile.source_type,
        "supplier_tariff_source_url": profile.source_url,
        "supplier_tariff_age_days": age,
        "supplier_settlement_resolution": resolution,
        # Keep the legacy name for dashboard compatibility; it is the schema version, not the registry revision.
        "supplier_registry_version": profile.registry_version,
        "supplier_registry_schema_version": profile.registry_version,
        "supplier_registry_revision": None if custom else profile.registry_revision,
        "supplier_registry_source": "custom" if custom else profile.registry_source,
    }
