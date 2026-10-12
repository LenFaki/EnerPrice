"""Deterministic, read-only maintainer audit and revision guard regressions."""

import copy
import json
import subprocess
import sys
import tomllib
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.nl_day_ahead_prices import registry_validation as validation
from custom_components.nl_day_ahead_prices import supplier_registry as registry
from scripts import audit_supplier_registry as audit
from scripts import release

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 9, 23)


def payload():
    return json.loads((ROOT / audit.REGISTRY_PATH).read_text())


def all_current():
    data = payload()
    for supplier in data["suppliers"].values():
        for tariff in supplier["tariffs"]:
            tariff["last_verified"] = TODAY.isoformat()
    return data


def row(report, key="anwb_energie"):
    return next(item for item in report["suppliers"] if item["supplier_id"] == key)


@pytest.mark.parametrize("age,expected", [
    (0, "current"), (60, "current"), (61, "verification_recommended"),
    (120, "verification_recommended"), (121, "stale"), (-1, "unknown"),
])
def test_shared_freshness_thresholds_and_exit_codes(age, expected):
    data = all_current()
    verified = (TODAY - timedelta(days=age)).isoformat()
    data["suppliers"]["anwb_energie"]["tariffs"][0]["last_verified"] = verified
    report = audit.audit_registry(data, TODAY)
    assert row(report)["status"] == expected
    assert row(report)["age_days"] == (age if age >= 0 else None)
    assert audit.exit_code(report) == (0 if expected == "current" else 1)
    assert audit.tariff_freshness is registry.tariff_freshness
    assert registry.SUPPLIER_TARIFF_VERIFY_WARNING_DAYS == 60
    assert registry.SUPPLIER_TARIFF_STALE_DAYS == 120


@pytest.mark.parametrize("verified,code", [(None, 1), ("invalid", 2), ("2026-02-30", 2)])
def test_missing_or_invalid_verification_unknown(verified, code):
    data = all_current()
    data["suppliers"]["anwb_energie"]["tariffs"][0]["last_verified"] = verified
    report = audit.audit_registry(data, TODAY)
    assert row(report)["status"] == "unknown"
    assert audit.exit_code(report) == code


@pytest.mark.parametrize("on,price", [("2026-09-29", 0.018), ("2026-09-30", 0.018),
                                     ("2026-10-01", 0.04), ("2026-10-02", 0.04)])
def test_inclusive_period_selection_history_and_future(on, price):
    data = payload()
    old = data["suppliers"]["anwb_energie"]["tariffs"][0]
    old["valid_until"] = "2026-09-30"
    data["suppliers"]["anwb_energie"]["tariffs"].append({
        **old, "valid_from": "2026-10-01", "valid_until": None,
        "purchase_fee_import": 0.04, "last_verified": "2026-09-23",
    })
    report = audit.audit_registry(data, date.fromisoformat(on))
    assert report["structurally_valid"]
    assert row(report)["purchase_fee_import"] == price
    assert audit.select_tariff is registry.select_tariff


def test_no_applicable_tariff_is_maintenance_not_structural_failure():
    data = all_current()
    data["suppliers"]["anwb_energie"]["tariffs"][0]["valid_from"] = "2026-10-01"
    report = audit.audit_registry(data, TODAY)
    assert report["structurally_valid"]
    assert row(report)["status"] == "unknown"
    assert "No applicable" in row(report)["problems"][0]
    assert audit.exit_code(report) == 1


@pytest.mark.parametrize("kind", ["overlap", "reversed", "empty", "missing", "multiple", "missing_field"])
def test_structural_problems(kind):
    data = payload()
    tariffs = data["suppliers"]["anwb_energie"]["tariffs"]
    if kind == "overlap":
        tariffs.append({**tariffs[0], "valid_from": "2026-09-01"})
    elif kind == "reversed":
        tariffs[0].update(valid_from="2026-10-01", valid_until="2026-09-01")
    elif kind == "empty":
        tariffs.clear()
    elif kind == "missing":
        del data["suppliers"]["anwb_energie"]
    elif kind == "missing_field":
        del tariffs[0]["source_type"]
    else:
        tariffs.append(copy.deepcopy(tariffs[0]))
    report = audit.audit_registry(data, TODAY)
    assert audit.exit_code(report) == 2
    assert report["problems"]
    assert row(report)["status"] == "unknown"


def test_additional_supplier_included_custom_ignored():
    data = all_current()
    data["suppliers"]["new_supplier"] = copy.deepcopy(data["suppliers"]["anwb_energie"])
    report = audit.audit_registry(data, TODAY)
    assert row(report, "new_supplier")["status"] == "current"
    assert len(report["suppliers"]) == 12
    assert not any(item["supplier_id"] == "custom" for item in report["suppliers"])


def test_audit_read_only_deterministic_order_and_fields():
    data = payload()
    original = copy.deepcopy(data)
    first = audit.audit_registry(data, TODAY)
    reordered = {**data, "suppliers": dict(reversed(list(data["suppliers"].items())))}
    second = audit.audit_registry(reordered, TODAY)
    assert data == original
    assert first == second
    assert audit.console_report(first) == audit.console_report(second)
    assert audit.markdown_report(first) == audit.markdown_report(second)
    assert first["summary"] == {"current": 1, "verification_recommended": 10, "stale": 0, "unknown": 0}
    assert first["suppliers"][0]["supplier_id"] == "easy_energy"
    assert first["suppliers"][-1]["supplier_id"] == "tibber"
    assert {"supplier_id", "supplier_name", "status", "last_verified", "age_days", "valid_from", "valid_until",
            "source_type", "source_url", "settlement_resolution", "registry_revision", "purchase_fee_import",
            "purchase_fee_export", "fixed_monthly_fee_electricity"} <= row(first).keys()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, False, 6, -1])
def test_numeric_failures_share_runtime_validation(value):
    data = payload()
    data["suppliers"]["anwb_energie"]["tariffs"][0]["purchase_fee_import"] = value
    assert audit.exit_code(audit.audit_registry(data, TODAY)) == 2
    assert audit.parse_remote_registry is validation.parse_remote_registry
    with pytest.raises(ValueError):
        validation.parse_remote_registry(data)


def test_shared_numeric_schema_bounds():
    schema = json.loads((ROOT / "registry/supplier_tariffs.schema.json").read_text())
    fields = schema["properties"]["suppliers"]["additionalProperties"]["properties"]["tariffs"]["items"]["properties"]
    for key, (minimum, maximum) in validation.NUMERIC_TARIFF_BOUNDS.items():
        assert (fields[key]["minimum"], fields[key]["maximum"]) == (minimum, maximum)


@pytest.mark.parametrize("change,revision_delta,valid", [
    (False, 0, True), (False, 1, True), (True, 0, False), (True, 1, True), (True, 3, True),
])
def test_revision_guard(change, revision_delta, valid):
    before = payload()
    after = copy.deepcopy(before)
    after["revision"] = before["revision"] + revision_delta
    if change:
        after["suppliers"]["anwb_energie"]["tariffs"][0]["purchase_fee_import"] = 0.02
    if valid:
        audit.check_revision(after, before)
    else:
        with pytest.raises(ValueError, match="higher revision"):
            audit.check_revision(after, before)


def test_revision_downgrade_and_semantic_equality():
    before = payload()
    before["revision"] = 4
    with pytest.raises(ValueError, match="decrease"):
        audit.check_revision(payload(), before)
    after = json.loads(json.dumps(before, sort_keys=True))
    after["published_at"] = "2026-09-24T00:00:00Z"
    for supplier in after["suppliers"].values():
        supplier["tariffs"].reverse()
    audit.check_revision(after, before)


def test_compare_ref_resolves_commit_without_shell_interpolation(monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout="a" * 40 if "rev-parse" in args else json.dumps(payload()).encode())
    monkeypatch.setattr(audit.subprocess, "run", run)
    ref = "origin/main; never-execute-this"
    audit.compare_ref(payload(), ref)
    assert calls[0][0] == ["git", "rev-parse", "--verify", "--end-of-options", ref + "^{commit}"]
    assert calls[1][0] == ["git", "show", "a" * 40 + ":registry/supplier_tariffs.json"]
    assert all(not kwargs.get("shell") for _, kwargs in calls)


@pytest.mark.parametrize("fmt", ["json", "console", "markdown"])
def test_cli_determinism_and_read_only(tmp_path, capsys, fmt):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(payload()))
    original = path.read_bytes()
    args = ["--registry", str(path), "--date", "2026-09-23", "--format", fmt]
    assert audit.main(args) == 1
    first = capsys.readouterr()
    assert audit.main(args) == 1
    second = capsys.readouterr()
    assert first.out == second.out
    assert first.err == second.err == ""
    assert path.read_bytes() == original
    if fmt == "json":
        assert json.loads(first.out)["audit_date"] == "2026-09-23"


@pytest.mark.parametrize("value", ["2026-02-30", "20260923", "bad", "2026-13-01"])
def test_invalid_cli_date_clean_failure(value, capsys):
    with pytest.raises(SystemExit) as error:
        audit.main(["--date", value])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert not output.out
    assert "Traceback" not in output.err


@pytest.mark.parametrize("contents", [b"PRIVATE_INVALID_CONTENT", b"[]", b'{"revision": NaN}', b'{"revision":Infinity}'])
def test_malformed_registry_json_exit_two(tmp_path, capsys, contents):
    path = tmp_path / "invalid.json"
    path.write_bytes(contents)
    assert audit.main(["--registry", str(path), "--format", "json"]) == 2
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert not report["structurally_valid"]
    assert "PRIVATE_INVALID_CONTENT" not in output.out + output.err


def test_validation_only_does_not_fail_on_freshness(capsys):
    assert audit.main(["--date", "2026-09-23", "--validate-only"]) == 0
    assert "verification_recommended: 10" in capsys.readouterr().out


def test_standard_library_only_execution():
    result = subprocess.run([sys.executable, "-S", str(ROOT / "scripts/audit_supplier_registry.py"),
                             "--date", "2026-09-23", "--validate-only", "--format", "json"],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["structurally_valid"]


def test_markdown_escapes_untrusted_input_and_omits_notes(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "PRIVATE_TOKEN")
    data = payload()
    supplier = data["suppliers"]["anwb_energie"]
    supplier["name"] = "<script>alert(1)</script>\n::error::boom | [click](javascript:x) @everyone"
    supplier["tariffs"][0]["notes"] = "PRIVATE_NOTES_HTTP_BODY"
    text = audit.markdown_report(audit.audit_registry(data, TODAY))
    assert text.startswith(audit.ISSUE_MARKER + "\n")
    assert "<script>" not in text and "@everyone" not in text
    assert "[click](javascript:x)" not in text
    assert not any(line.startswith("::") for line in text.splitlines())
    assert "PRIVATE" not in text
    assert "Preserve historical records" in text
    assert audit.safe_url("https://example.com") == "https://example.com"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://user:secret@example.com", "https://[bad", "https://example.com/\n::error"])
def test_unsafe_links_not_rendered(url):
    assert audit.safe_url(url) is None


def test_manifest_and_project_versions_match():
    manifest = (ROOT / release.MANIFEST).read_text()
    manifest_version = json.loads(manifest)["version"]
    project_version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert manifest_version == project_version


def test_workflow_security_schedule_and_release_job_unchanged():
    workflow = (ROOT / ".github/workflows/supplier-tariff-audit.yml").read_text()
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert 'cron: "17 6 * * 1"' in workflow
    assert "contents: write" not in workflow
    assert "issues: write" in workflow and "contents: read" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert 'if [ "$status" -gt 1 ]' in workflow
    assert "pull_request_target" not in workflow + ci
    assert 'run: python scripts/audit_supplier_registry.py --validate-only --compare-ref "$BASE_REF"' in ci
    assert "issues: write" not in ci
    release_job = ci.split("  release:\n", 1)[1]
    assert "needs: lint-test" in release_job
    assert "python scripts/release.py --publish" in release_job
