"""Small, public-only registry diagnostic views shared by sensors and diagnostics."""

from urllib.parse import urlparse

from .const import SUPPLIER_UPDATES_AUTOMATIC
from .supplier_registry import load_registry, tariff_metadata


def registry_status(manager, mode):
    enabled = mode == SUPPLIER_UPDATES_AUTOMATIC
    active = manager.active if manager is not None and enabled else load_registry()
    source = manager.source if manager is not None and enabled else "bundled"
    def timestamp(value):
        return value.isoformat() if value is not None else None
    return {
        "mode": mode,
        "active_source": source,
        "schema_version": active.version,
        "revision": active.revision,
        "published_at": active.published_at,
        "last_checked": timestamp(manager.last_check) if manager else None,
        "last_successful_update": timestamp(manager.last_success) if manager else None,
        "last_registry_update": timestamp(manager.last_update) if manager else None,
        "remote_updates_enabled": enabled,
        "fallback_active": enabled and source != "remote",
        "last_check_result": manager.last_check_result if manager and enabled else (
            "never_checked" if enabled else "disabled"
        ),
    }


def tariff_status(profile, now, manager=None):
    metadata = tariff_metadata(profile, now)
    source = metadata["supplier_registry_source"]
    if source == "remote" and manager is not None:
        source = manager.source
    # Legacy/custom profiles can contain arbitrary user strings; expose no such URL.
    url = profile.source_url if source in {"remote", "cached_remote", "bundled"} else None
    try:
        parsed = urlparse(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            url = None
    except ValueError:
        url = None
    return {
        "supplier": profile.key,
        "freshness": metadata["supplier_tariff_status"],
        "registry_source": source,
        "registry_revision": profile.registry_revision if source != "custom" else None,
        "purchase_fee_import": profile.purchase_fee_import,
        "purchase_fee_export": profile.purchase_fee_export,
        "fixed_monthly_fee_electricity": profile.fixed_monthly_fee_electricity,
        "vat_included": profile.purchase_fee_includes_vat,
        "export_vat_included": profile.sell_fee_includes_vat,
        "settlement_resolution": metadata["supplier_settlement_resolution"],
        "valid_from": profile.valid_from,
        "valid_until": profile.valid_until,
        "last_verified": profile.last_verified,
        "verification_age_days": metadata["supplier_tariff_age_days"],
        "source_type": metadata["supplier_tariff_source_type"],
        "source_url": url,
    }
