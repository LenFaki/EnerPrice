"""Read-only, standard-library supplier audit using EnerPrice's shared rules."""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

# Direct script execution must also resolve the repository's pure Python modules.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_components.nl_day_ahead_prices.registry_validation import (  # noqa: E402
    REQUIRED_SUPPLIERS,
    decode_registry,
    parse_remote_registry,
    validate_periods,
)
from custom_components.nl_day_ahead_prices.supplier_registry import (  # noqa: E402
    parse_registry,
    select_tariff,
    tariff_freshness,
)

REGISTRY_PATH = "registry/supplier_tariffs.json"
ISSUE_MARKER = "<!-- enerprice-supplier-tariff-audit -->"
ISSUE_TITLE = "EnerPrice supplier tariff audit"
STATUS_ORDER = ("unknown", "stale", "verification_recommended", "current")
STRUCTURAL_ERRORS = (ValueError, TypeError, KeyError, OverflowError, RecursionError)


def iso_date(value: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise argparse.ArgumentTypeError("Date must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError("Invalid calendar date") from err


def safe_url(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return None
    try:
        url = urlparse(value)
        if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password:
            return None
        _ = url.port
    except ValueError:
        return None
    return value


def empty_report(on: date):
    return {
        "audit_date": on.isoformat(), "registry_revision": None, "schema_version": None,
        "structurally_valid": False, "maintenance_required": True,
        "summary": dict.fromkeys(STATUS_ORDER, 0), "suppliers": [], "problems": [],
    }


def audit_registry(payload, on: date):
    """Report partial structural findings, but never accept invalid data."""
    report = empty_report(on)
    if not isinstance(payload, dict):
        report["problems"].append("Registry must be an object.")
        return report
    for source, target in (("revision", "registry_revision"), ("schema_version", "schema_version")):
        value = payload.get(source)
        report[target] = value if type(value) is int and value > 0 else None
    parsed = None
    try:
        parsed = parse_remote_registry(payload)
        report["structurally_valid"] = True
    except STRUCTURAL_ERRORS:
        report["problems"].append("Registry failed shared runtime validation.")
    suppliers = payload.get("suppliers")
    suppliers = suppliers if isinstance(suppliers, dict) else {}
    keys = REQUIRED_SUPPLIERS | {key for key in suppliers if isinstance(key, str) and key != "custom"}
    for key in sorted(keys):
        supplier = suppliers.get(key)
        row = {
            "supplier_id": key, "supplier_name": key, "status": "unknown",
            "last_verified": None, "age_days": None, "valid_from": None, "valid_until": None,
            "source_type": None, "source_url": None, "settlement_resolution": None,
            "registry_revision": report["registry_revision"], "purchase_fee_import": None,
            "purchase_fee_export": None, "fixed_monthly_fee_electricity": None, "problems": [],
        }
        if isinstance(supplier, dict) and isinstance(supplier.get("name"), str):
            row["supplier_name"] = supplier["name"]
        if key not in suppliers:
            row["problems"].append("Required supplier is missing.")
        elif not isinstance(supplier, dict) or not supplier.get("tariffs"):
            row["problems"].append("Supplier has no tariff records.")
        else:
            try:
                # The strict full-candidate validator remains authoritative. On failure,
                # reuse the legacy parser only to provide readable structural diagnostics.
                local = parsed or parse_registry({
                    "registry_version": 1, "country": "NL", "currency": "EUR", "suppliers": {key: supplier},
                })
                validate_periods(local.suppliers[key])
                profile = select_tariff(local, key, on)
                if profile is None:
                    row["problems"].append("No applicable tariff on the audit date.")
                else:
                    status, age = tariff_freshness(profile.last_verified, on)
                    row.update({
                        "status": status, "age_days": age, "last_verified": profile.last_verified,
                        "valid_from": profile.valid_from, "valid_until": profile.valid_until,
                        "source_type": profile.source_type, "source_url": safe_url(profile.source_url),
                        "settlement_resolution": profile.default_settlement_resolution,
                        "purchase_fee_import": profile.purchase_fee_import,
                        "purchase_fee_export": profile.purchase_fee_export,
                        "fixed_monthly_fee_electricity": profile.fixed_monthly_fee_electricity,
                    })
            except STRUCTURAL_ERRORS:
                row["problems"].append("Invalid supplier fields, reversed or overlapping tariff periods.")
        report["suppliers"].append(row)
        report["summary"][row["status"]] += 1
        report["problems"].extend(f"{key}: {problem}" for problem in row["problems"])
    report["suppliers"].sort(key=lambda row: (
        STATUS_ORDER.index(row["status"]),
        row["last_verified"] if row["age_days"] is not None else "",
        row["supplier_id"],
    ))
    report["maintenance_required"] = bool(report["problems"]) or any(
        report["summary"][status] for status in STATUS_ORDER if status != "current"
    )
    return report


def exit_code(report):
    if not report["structurally_valid"]:
        return 2
    return 1 if report["maintenance_required"] else 0


def escape(value):
    """Render untrusted data as single-line text, never markup/mentions/commands."""
    if value is None:
        return "unknown"
    value = " ".join(str(value).split())
    value = "".join(c for c in value if c.isprintable())
    value = html.escape(value, quote=True).replace("@", "&#64;")
    return re.sub(r"([\\`*_{}\[\]()#+.!|~])", r"\\\1", value)


def markdown_report(report):
    lines = [ISSUE_MARKER, f"# {ISSUE_TITLE}", "",
             f"Last audit: {report['audit_date']}",
             f"Registry revision: {report['registry_revision']}", "",
             "Generated read-only report. Freshness is not proof of tariff correctness.", "",
             "## Needs verification", "",
             "| Supplier | Status | Last verified | Age (days) | Valid period | Source |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in report["suppliers"]:
        if row["status"] == "current":
            continue
        url = safe_url(row["source_url"])
        source = f"[source](<{quote(url, safe=':/?#%=&+;,@')}>)" if url else "unknown"
        period = f"{row['valid_from'] or 'unbounded'} to {row['valid_until'] or 'unbounded'}"
        lines.append("| " + " | ".join([
            escape(f"{row['supplier_name']} ({row['supplier_id']})"), escape(row["status"]),
            escape(row["last_verified"]), escape(row["age_days"]), escape(period), source,
        ]) + " |")
    if not report["maintenance_required"]:
        lines.append("\nAll applicable supplier records are current; no maintenance findings.")
    lines.extend(["", "## Current", "", "| Supplier | Last verified | Age (days) |", "| --- | --- | --- |"])
    for row in report["suppliers"]:
        if row["status"] == "current":
            lines.append(f"| {escape(row['supplier_name'])} | {escape(row['last_verified'])} | {row['age_days']} |")
    lines.extend(["", "## Problems", ""])
    lines.extend(f"- {escape(problem)}" for problem in report["problems"])
    if not report["problems"]:
        lines.append("None.")
    lines.extend(["", "## Summary", ""])
    lines.extend(f"- {status}: {report['summary'][status]}" for status in STATUS_ORDER)
    lines.extend(["", "## Maintainer instructions", "",
                  "1. Open the public supplier source and verify the applicable contract manually.",
                  "2. Marketing examples or a reachable URL do not prove a tariff amount.",
                  "3. Preserve historical records; add a period only with a proven effective date.",
                  "4. Update last_verified only after genuine verification, preserving source provenance.",
                  "5. Increment the registry revision when semantic supplier content changes.",
                  "6. Run validation, revision checks, the complete tests and Ruff.",
                  "7. Open a normal reviewed PR. This automation never edits tariff data.", "",
                  "A stale record needs human review; it does not automatically mean its price is wrong."])
    return "\n".join(lines) + "\n"


def console_report(report):
    lines = [ISSUE_TITLE, f"Audit date: {report['audit_date']}",
             f"Registry revision: {report['registry_revision']}"]
    for status in STATUS_ORDER:
        lines.extend(["", status.upper().replace("_", " ")])
        for row in report["suppliers"]:
            if row["status"] == status:
                lines.append(f"* {escape(row['supplier_name'])} ({escape(row['supplier_id'])}) - "
                             f"verified {escape(row['last_verified'])} - {escape(row['age_days'])} days")
                lines.append(f"  period {escape(row['valid_from'])} to {escape(row['valid_until'])}; "
                             f"{escape(row['source_type'])}; {escape(row['settlement_resolution'])}; "
                             f"source {escape(row['source_url'])}")
                lines.append(f"  import {row['purchase_fee_import']}; export {row['purchase_fee_export']}; "
                             f"monthly {row['fixed_monthly_fee_electricity']}")
    lines.extend(["", "Summary:", *(f"{status}: {report['summary'][status]}" for status in STATUS_ORDER)])
    lines.extend(f"Problem: {escape(problem)}" for problem in report["problems"])
    return "\n".join(lines) + "\n"


def semantic_content(payload):
    """Ignore formatting, object order, period order and publication-only metadata."""
    result = {key: value for key, value in payload.items() if key not in {"revision", "published_at"}}
    result["suppliers"] = {key: {**value, "tariffs": sorted(
        value["tariffs"], key=lambda record: json.dumps(record, sort_keys=True),
    )} for key, value in payload["suppliers"].items()}
    return result


def check_revision(current, previous):
    parse_remote_registry(current)
    parse_remote_registry(previous)
    if current["revision"] < previous["revision"]:
        raise ValueError("Registry revision must not decrease.")
    if semantic_content(current) != semantic_content(previous) and current["revision"] <= previous["revision"]:
        raise ValueError("Changed registry content requires a higher revision.")


def compare_ref(current, ref, root=ROOT):
    """Read only a resolved commit; no shell interpolation or ref-based options."""
    commit = subprocess.run(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise ValueError("Invalid comparison commit.")
    raw = subprocess.run(["git", "show", f"{commit}:{REGISTRY_PATH}"], cwd=root,
                         check=True, capture_output=True).stdout
    check_revision(current, decode_registry(raw))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=iso_date, default=None)
    parser.add_argument("--format", choices=["console", "json", "markdown"], default="console")
    parser.add_argument("--registry", type=Path, default=ROOT / REGISTRY_PATH)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--compare-ref", help="Exact git commit/ref to compare, e.g. origin/main or a merge-base SHA")
    args = parser.parse_args(argv)
    on = args.date or datetime.now(ZoneInfo("Europe/Amsterdam")).date()
    try:
        payload = decode_registry(args.registry.read_bytes())
        report = audit_registry(payload, on)
    except (OSError, *STRUCTURAL_ERRORS):
        report = empty_report(on)
        report["problems"].append("Registry file is unreadable or invalid JSON.")
    else:
        if args.compare_ref and report["structurally_valid"]:
            try:
                compare_ref(payload, args.compare_ref)
            except (OSError, subprocess.SubprocessError, *STRUCTURAL_ERRORS):
                report["structurally_valid"] = False
                report["maintenance_required"] = True
                report["problems"].append("Revision guard failed: require a valid base, no downgrade and an increment for changed content.")
    if args.format == "json":
        output = json.dumps(report, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    elif args.format == "markdown":
        output = markdown_report(report)
    else:
        output = console_report(report)
    sys.stdout.write(output)
    return (0 if report["structurally_valid"] else 2) if args.validate_only else exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
