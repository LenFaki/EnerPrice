# EnerPrice remote supplier registry

This directory publishes static public tariff data for the remote-registry core.
It is not a live supplier scraper. Initial revision 1 exactly copies the v2.1.1
bundled supplier data, including historical periods and conservative provenance.
The integration version remains unchanged for this infrastructure PR.

## Schema and revisions

- `schema_version: 1` identifies the compatible data format. Change it only for
  incompatible format changes, together with integration support.
- `revision` is a positive integer identifying a complete data snapshot. Increase
  it for every verified data change, independently of integration releases.
- Optional `published_at` is an explicit UTC ISO-8601 publication timestamp.
  It is not a supplier verification date or contract effective date.
- `country: NL` and `currency: EUR` are fixed. Fees use EUR/kWh and EUR/month.
- `supplier_tariffs.schema.json` describes structural requirements. Runtime
  validation additionally checks period ordering, overlap, finite numbers and
  source URL semantics. Use the runtime validator as the final authority.

All eleven built-in commercial supplier IDs must be present. A custom entry may
be included for compatibility but remote custom values are never selected.
User custom configuration always has precedence.

## Safety bounds

The downloader accepts at most 1 MiB, including streamed/decompressed data, with
a 20-second total timeout. It does not follow redirects. The registry allows
at most 100 suppliers and 100 tariff periods per supplier. Supplier names are
limited to 200 characters, source URLs to 2048 and notes to 10000.

Per-kWh fees must be finite and between -5 and +5 EUR/kWh; import fees must also
be nonnegative. Monthly fees must be between 0 and 1000 EUR inclusive. These
deliberately broad limits catch obvious decimal/unit mistakes, not market moves.
Negative export fees are supported. Booleans cannot substitute for numbers.

## Safe maintainer workflow

1. Verify a supplier's actual tariff against a primary source and its contract
   scope. Establish the effective date before introducing a validity boundary;
   a website observation date alone is not an effective date.
2. Edit `registry/supplier_tariffs.json`, keeping amounts and VAT flags precise.
3. Preserve historical periods; endpoints are inclusive and may not overlap.
4. Update source/provenance notes. Do not promote secondary values to official
   or advance the complete-record verification date after a partial check.
5. Increment `revision` and set `published_at` to the actual UTC publication time.
6. Run the complete test suite and Ruff. Validate the candidate locally:

   ```bash
   python scripts/audit_supplier_registry.py --validate-only --compare-ref origin/main
   pytest
   ruff check .
   ```

7. Review and merge the data change. No integration version bump is necessary
   for a remote-only data update; the existing release pipeline is unchanged.
8. Installations with this core receive newer compatible revisions at their next
   scheduled check, normally within 24 hours. Offline installations retain local
   data until a later successful check.

To revert erroneous data, publish corrected data under a **higher** revision.
Do not decrement the revision or edit content under an already published one.
When refreshing the bundled file in a future integration release, include its
matching `revision`; older bundled files without that field mean revision 1.
Do not renumber an unchanged bundled snapshot.

See [runtime architecture](../docs/supplier-tariff-registry.md#remote-registry-core-pr1)
for startup, storage, fallback and privacy behavior. PR2 provides per-entry
Automatic/Bundled-only options and diagnostic entities.

## Read-only maintenance audit (PR3)

The standard-library CLI shares the runtime's strict validator, period selector
and freshness function; Home Assistant and network access are not required:

```bash
python scripts/audit_supplier_registry.py --date 2026-09-23
python scripts/audit_supplier_registry.py --date 2026-09-23 --format json
python scripts/audit_supplier_registry.py --date 2026-09-23 --format markdown
python scripts/audit_supplier_registry.py --validate-only
python scripts/audit_supplier_registry.py --validate-only --compare-ref origin/main
```

Without `--date`, the audit uses today's Europe/Amsterdam date. Explicit dates
must be valid ISO YYYY-MM-DD dates. Console output groups suppliers by maintenance
severity, then oldest verification and supplier ID. JSON contains metadata,
summary, supplier records and problems, without extra stdout logging. Markdown
is the deterministic issue body. All formats are read-only.

Only the tariff applicable on the audit date is audited. Boundaries are inclusive:
`valid_from <= date <= valid_until`, with null meaning unbounded. Historical and
future records are not substituted for a missing applicable record. Additional
supplier IDs are included automatically; `custom` is excluded.

| Status | Verification age |
| --- | --- |
| current | 0-60 days |
| verification_recommended | 61-120 days |
| stale | More than 120 days |
| unknown | Null, invalid or future verification date, or no applicable tariff |

Exit codes: **0** means structurally valid and all suppliers current; **1** means
valid with maintenance findings (including verification recommended); **2** means
invalid structure, execution failure or revision-guard failure. Invalid non-null
date strings are reported as unknown but also fail strict validation with code 2.
Missing required fields, missing suppliers, empty records, reversed dates and
overlaps are structural failures. A valid history with a gap returns code 1.
`--validate-only` ignores freshness and returns 0 or 2.

`--compare-ref` reads the base registry through Git without changing files.
Semantic content changes require an increased revision; decreases always fail.
Object ordering, tariff-array ordering and formatting do not matter. Changes to
`published_at` alone do not require an increment. Revision jumps are allowed but
should be deliberate. An invalid/unavailable base fails safely and requires
maintainer investigation. PR CI compares against the PR base commit.

The weekly workflow runs Monday **06:17 UTC**, or manually on upstream `main`.
It validates and tests first, saves JSON/Markdown artifacts, and accepts exit 1
as a successful audit. Exit 2 fails before issue publication. Permissions are
`contents: read` and `issues: write`; checkout credentials are not persisted.

One central issue uses title **EnerPrice supplier tariff audit**, optional label
`supplier-tariff-audit`, and marker `<!-- enerprice-supplier-tariff-audit -->`.
Only marked issues with the title or label are managed. Open and closed issues
are searched: findings create/update/reopen the issue, healthy results update
and close it. A healthy first run creates nothing. Multiple matches generate a
warning and only the oldest is updated; there is no mass closure. The label is
created when possible, with title/marker fallback on permission failure. Reports
replace the body rather than accumulating comments.

Supplier text is escaped, public source links validated, and notes/raw errors
and environment secrets are not included. No supplier URL is fetched. No scraper,
AI interpretation, tariff edit, revision increment, commit, automated tariff PR
or release runs here. Verify complete contract amounts, VAT and effective dates
manually; preserve history and provenance and submit a normal reviewed PR.

**Freshness is not correctness:** recently verified means recently checked by a
maintainer, not proof that a supplier has not changed prices since. Stale means
verification is needed, not that the amount is necessarily wrong.
