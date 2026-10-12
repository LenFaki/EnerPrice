"""Release decisions and publication orchestration; never call GitHub or git."""

import json
import subprocess

import pytest

from scripts import release


def published(version, *, prerelease=False, draft=False):
    return {"tag_name": f"v{version}", "prerelease": prerelease, "draft": draft}


@pytest.mark.parametrize("version,latest,expected", [
    ("2.1.0", "2.0.3", True), ("2.1.0", "2.1.0", False),
    ("2.10.0", "2.9.0", True), ("2.1.1", "2.1.0", True),
])
def test_decisions(version, latest, expected):
    assert release.release_decision(version, [published(latest)], False)[0] == expected


def test_older_version_rejected():
    with pytest.raises(ValueError, match="older"):
        release.release_decision("2.0.3", [published("2.1.0")], False)


@pytest.mark.parametrize("version", ["2.1", "v2.1.0", "02.1.0", "2.1.0-01", "2.1.0;echo", "", None])
def test_invalid_versions(version):
    with pytest.raises(ValueError):
        release.release_decision(version, [], False)


def test_idempotency_no_release_and_semver_order():
    assert release.release_decision("2.1.0", [], True)[0] is False
    assert release.release_decision("2.1.0", [published("2.1.0", draft=True)], False)[0] is False
    assert release.release_decision("2.1.0", [], False)[0] is True
    assert release.release_decision("2.1.0+build2", [published("2.1.0+build1")], False)[0] is False
    versions = ["2.1.0-alpha", "2.1.0-alpha.1", "2.1.0-alpha.beta", "2.1.0-beta", "2.1.0-beta.2", "2.1.0-beta.11", "2.1.0-rc.1", "2.1.0"]
    assert sorted(reversed(versions), key=release.version_key) == versions
    with pytest.raises(ValueError, match="older"):
        release.release_decision("2.1.0-rc.1", [published("2.1.0-rc.2", prerelease=True)], False)


def context(monkeypatch, event="push"):
    for key, value in {"GITHUB_REPOSITORY": release.REPOSITORY, "GITHUB_REF": "refs/heads/main",
                       "GITHUB_EVENT_NAME": event, "GITHUB_SHA": "a" * 40, "BEFORE_SHA": "b" * 40,
                       "CI_RESULT": "success"}.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("field,value", [("GITHUB_REF", "refs/heads/feature"), ("CI_RESULT", "failure"),
    ("CI_RESULT", "cancelled"), ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_REPOSITORY", "fork/repo")])
def test_invalid_context_never_runs_commands(monkeypatch, field, value):
    context(monkeypatch)
    monkeypatch.setenv(field, value)
    monkeypatch.setattr(release, "run", lambda *args: pytest.fail("No command allowed"))
    with pytest.raises(ValueError):
        release.execute(publish=True)


def runner(monkeypatch, version="2.1.0", previous="2.0.3", failure=None):
    calls = []

    def run(*args):
        calls.append(args)
        if failure and args[:len(failure)] == failure:
            raise subprocess.CalledProcessError(1, args)
        if args[:2] == ("git", "rev-parse"):
            return "a" * 40
        if args[:2] == ("git", "show"):
            return json.dumps({"version": previous if args[2].startswith("b" * 40) else version})
        if args[:2] == ("gh", "api"):
            return json.dumps([[published("2.0.3")]])
        return ""

    monkeypatch.setattr(release, "run", run)
    return calls


def test_unchanged_version_merge_never_releases(monkeypatch):
    context(monkeypatch)
    calls = runner(monkeypatch, previous="2.1.0")
    release.execute(publish=True)
    assert not any(call[0] == "gh" or call[:2] == ("git", "tag") for call in calls)


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
@pytest.mark.parametrize("version,prerelease", [("2.1.0", False), ("2.1.0-rc.1", True)])
def test_exact_tested_sha_and_prerelease_flags(monkeypatch, event, version, prerelease):
    context(monkeypatch, event)
    calls = runner(monkeypatch, version=version)
    release.execute(publish=True)
    assert ("git", "tag", f"v{version}", "a" * 40) in calls
    assert ("git", "push", "origin", f"refs/tags/v{version}:refs/tags/v{version}") in calls
    command = calls[-1]
    assert command[:3] == ("gh", "release", "create")
    assert command[command.index("--target") + 1] == "a" * 40
    assert "--verify-tag" in command
    assert ("--prerelease" in command) == prerelease
    assert not any("--force" in call for call in calls)


@pytest.mark.parametrize("failure", [("gh", "api"), ("git", "push"), ("git", "merge-base")])
def test_failure_aborts_before_release(monkeypatch, failure):
    context(monkeypatch)
    calls = runner(monkeypatch, failure=failure)
    with pytest.raises(subprocess.CalledProcessError):
        release.execute(publish=True)
    assert not any(call[:3] == ("gh", "release", "create") for call in calls)


def test_default_dry_run_no_mutations(monkeypatch):
    context(monkeypatch)
    calls = runner(monkeypatch)
    release.execute()
    assert not any(call[:2] in {("git", "tag"), ("git", "push"), ("gh", "release")} for call in calls)


def test_manual_bootstrap_and_missing_base():
    assert release.automatic_change_required("workflow_dispatch", "2.1.0", None)
    with pytest.raises(ValueError):
        release.automatic_change_required("push", "2.1.0", None)
