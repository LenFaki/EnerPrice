# EnerPrice releases

## Normal development

1. Create a feature branch from main.
2. Bump `custom_components/nl_day_ahead_prices/manifest.json` to the intended
   SemVer version. Keep `pyproject.toml` aligned according to project convention;
   only the manifest drives release automation.
3. Prepare CHANGELOG.md in the PR, review, then merge.
4. Wait for the `CI` workflow's `lint-test` job to pass on main.
5. Its dependent `release` job creates `v<version>` and `EnerPrice v<version>`
   using GitHub-generated release notes. The changelog is not rewritten.

Do not create the tag yourself before automation runs. No automatic version
increment occurs. No deployment or integration files are rewritten.

## Triggers and safety

The existing `.github/workflows/ci.yml` handles push to main, PR validation,
and input-free `workflow_dispatch`. The release job requires `lint-test` and
is restricted to the upstream repository, main ref, and push/manual events.
PR runs and feature-branch manual runs never receive release-job permissions.
Every manual run reruns tests and Ruff; it cannot supply a version or bypass CI.

On push, compare the tested manifest's version to the manifest at the pre-push
commit (`github.event.before`). An unchanged version always skips, even if that
version has not yet been published. This includes the initial workflow PR.
Missing pre-push history blocks automatic publishing; manual recovery is explicit.

The release job checks out `github.sha`, verifies HEAD and membership in main's
history, and tags that exact tested commit, never an implicitly resolved later
main. A later main commit does not move the release's target.

Workflow permissions are `contents: read`; only the release job has
`contents: write`. `GH_TOKEN` is the built-in GitHub token. Publication uses Git
and GitHub CLI, without third-party release actions. Job-level concurrency group
`enerprice-release` has `cancel-in-progress: false`. GitHub may replace a pending
job when several runs queue; manual recovery can publish a missed version.

The script validates strict SemVer (including optional prerelease/build metadata)
and uses numeric precedence, not string ordering. Published releases are fully
paginated; the highest stable semantic version is the baseline, not merely the
most recently created release. Older published prereleases also block backwards
publication. Malformed published version tags block comparison for human review.
Build metadata does not change precedence. With no previous release, the first
eligible version may publish.

An existing remote tag or matching release (including a draft) always skips.
Otherwise equal versions skip, lower versions fail, and higher versions proceed.
Only the new tag is pushed, without force, and `gh release create --verify-tag`
cannot manufacture a missing tag. Stable versions publish as latest, non-draft,
non-prerelease. Explicit `-rc.1`/other SemVer prereleases use `--prerelease` and
are never marked latest. API/auth/network failures abort rather than looking
like an empty release history. Repeated runs cannot overwrite a release/tag.

## Manual recovery and initial 2.1.0 bootstrap

The workflow implementation leaves manifest version 2.1.0 unchanged, so merging
it does not publish 2.1.0 automatically.

After reviewing and merging this workflow PR:

1. Open GitHub **Actions > CI > Run workflow**.
2. Select **main**; there are no version inputs.
3. Start the run. It runs the complete test suite and Ruff on the chosen main
   commit before considering a release.
4. If v2.1.0 has no existing tag/release and exceeds the published versions,
   the dependent job tags that tested commit and publishes v2.1.0.
5. Check the release URL and tag SHA in the job logs. Future manifest bumps on
   main are automatic.

Equivalent explicit command (only after merge, when publication is intended):

```sh
gh workflow run ci.yml --repo LenFaki/home-assistant-nl-day-ahead-prices --ref main
```

If publication failed before tag creation, rerun CI on main using that manual
trigger. If a tag was pushed but release creation failed, the pipeline deliberately
skips that existing tag on retries. Inspect its exact commit and successful CI,
then finish publishing through a deliberate maintainer action using that existing
tag. Never move or delete a published tag to make automation run again. Existing
drafts likewise require explicit review; automation never republishes them.

## HACS and validation

Existing EnerPrice releases use GitHub source archives and have no custom assets.
`hacs.json` has `content_in_root: false` and does not enable `zip_release`.
The single integration remains at `custom_components/nl_day_ahead_prices`, with
its manifest in the same place. The pipeline preserves this distribution model,
repository identity and integration domain; no ZIP build step is needed.

`tests/test_release.py` checks semantic precedence, duplicate detection, safety
gates, unchanged-version merges, manual bootstrap, exact commit targets,
prerelease flags, dry runs and failed publication steps. Every subprocess in
orchestration tests is mocked: tests never create real tags or releases.

References: [GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax),
[gh release create](https://cli.github.com/manual/gh_release_create),
[HACS integration structure](https://www.hacs.dev/docs/publish/integration/).
