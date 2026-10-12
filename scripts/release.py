"""Conservative release decision and official Git/gh publication tooling.

No source writes. Default execution is dry-run; CI passes --publish only after
the test job succeeds. All subprocess failures abort before subsequent writes.
"""

from __future__ import annotations

# ruff: noqa: T201 -- explicit release decision logs belong in Actions output.
import argparse
import json
import os
import re
import subprocess
from typing import Any

MANIFEST = "custom_components/nl_day_ahead_prices/manifest.json"
REPOSITORY = "LenFaki/home-assistant-nl-day-ahead-prices"
SEMVER = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)


def version_key(version: str) -> tuple:
    """SemVer precedence: numeric components, prerelease identifiers, no build metadata."""
    match = SEMVER.fullmatch(version) if isinstance(version, str) else None
    if match is None:
        raise ValueError(f"Invalid semantic version: {version!r}")
    major, minor, patch, prerelease, _ = match.groups()
    identifiers = []
    for item in prerelease.split(".") if prerelease else []:
        if item.isdigit() and len(item) > 1 and item.startswith("0"):
            raise ValueError("Numeric prerelease identifiers cannot have leading zeroes")
        identifiers.append((0, int(item)) if item.isdigit() else (1, item))
    return int(major), int(minor), int(patch), prerelease is None, tuple(identifiers)


def manifest_version(text: str) -> str:
    version = json.loads(text)["version"]
    version_key(version)
    return version


def release_decision(version: str, releases: list[dict[str, Any]], tag_exists: bool) -> tuple[bool, str]:
    candidate = version_key(version)
    tag = f"v{version}"
    if tag_exists or any(item["tag_name"] == tag for item in releases):
        return False, f"EnerPrice {tag} tag/release already exists - nothing to do."
    stable = []
    published = []
    for item in releases:
        if item["draft"]:
            continue
        name = item["tag_name"]
        key = version_key(name.removeprefix("v"))
        published.append((key, name))
        if not item["prerelease"] and key[3]:
            stable.append((key, name))
    latest = max(stable, default=None)
    print(f"Latest stable release: {latest[1] if latest else 'none'}")
    if latest and candidate < latest[0]:
        raise ValueError(f"Manifest version {version} is older than latest release {latest[1]} - refusing release.")
    if latest and candidate == latest[0]:
        return False, "Equivalent version already released - nothing to do."
    # Prevent publishing an older prerelease too, even when stable is further behind.
    newest = max(published, default=None)
    if newest and candidate < newest[0]:
        raise ValueError(f"Version {version} is older than published version {newest[1]} - refusing release.")
    if newest and candidate == newest[0]:
        return False, "Equivalent version already published - nothing to do."
    return True, "Release required: yes"


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def validate_context(env: dict[str, str]) -> str:
    if env.get("GITHUB_REPOSITORY") != REPOSITORY or env.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Releases are restricted to the upstream main branch")
    if env.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"} or env.get("CI_RESULT") != "success":
        raise ValueError("Successful CI on main is required")
    sha = env.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Missing exact tested commit SHA")
    return sha


def automatic_change_required(event: str, version: str, previous_manifest: str | None) -> bool:
    if event == "workflow_dispatch":
        return True
    if event != "push" or previous_manifest is None:
        raise ValueError("Cannot establish previous manifest; use explicit manual recovery")
    return manifest_version(previous_manifest) != version


def execute(*, publish: bool = False) -> None:
    sha = validate_context(dict(os.environ))
    if run("git", "rev-parse", "HEAD") != sha:
        raise ValueError("Checkout does not match tested commit")
    run("git", "fetch", "origin", "main")
    run("git", "merge-base", "--is-ancestor", sha, "FETCH_HEAD")
    version = manifest_version(run("git", "show", f"{sha}:{MANIFEST}"))
    tag = f"v{version}"
    print(f"EnerPrice manifest version: {version}\nExpected tag: {tag}\nCI commit: {sha}")
    previous = None
    event = os.environ["GITHUB_EVENT_NAME"]
    if event == "push":
        before = os.environ.get("BEFORE_SHA", "")
        if not re.fullmatch(r"[0-9a-f]{40}", before) or before == "0" * 40:
            raise ValueError("No valid pre-push commit; use explicit manual recovery")
        previous = run("git", "show", f"{before}:{MANIFEST}")
    if not automatic_change_required(event, version, previous):
        print("Release required: no - manifest version unchanged in this push.")
        return
    # Pagination is essential: old releases/drafts must also prevent duplicates.
    pages = json.loads(run("gh", "api", "--paginate", "--slurp", f"repos/{REPOSITORY}/releases?per_page=100"))
    releases = [release for page in pages for release in page]
    exists = bool(run("git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}"))
    required, reason = release_decision(version, releases, exists)
    print(reason)
    if not required or not publish:
        if required:
            print("Dry-run: no tag or release created.")
        return
    # No force flag: a competing/existing remote tag aborts rather than overwrites.
    run("git", "tag", tag, sha)
    run("git", "push", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    command = ["gh", "release", "create", tag, "--repo", REPOSITORY,
               "--verify-tag", "--target", sha, "--title", f"EnerPrice {tag}", "--generate-notes"]
    if version_key(version)[3]:
        command.append("--latest=true")
    else:
        command.extend(["--prerelease", "--latest=false"])
    print(run(*command))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    try:
        execute(publish=args.publish)
    except (ValueError, KeyError, subprocess.CalledProcessError) as error:
        # Do not echo subprocess stderr or environment values containing credentials.
        raise SystemExit(f"Release blocked: {error}") from None
