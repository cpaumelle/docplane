# Invariant catalogue importer

Invariants are governed records. Their authoritative source is structured YAML in
git — one file per domain — reviewed through pull requests; git is the history and
audit trail. `scripts/invariant_catalogue.py` projects that source into DocPlane,
following the meter-list exemplar exactly:

| Layer | What the importer writes |
|---|---|
| MODEL | one `INVARIANT` entity per record, key = lower-cased id (`i-coverage-derived-1`), URI `docplane://model/invariant/<key>`; current state only |
| KNOW | one generated catalogue page per domain at `<INVARIANT_SECTION>/<domain>.md` |
| MODEL | one artifact declaration (`invariant-catalogue`) owning those pages; source entity `SYSTEM/invariant-registry` |
| MODEL | exact `INVARIANT -> domain page` CATALOGUES links |
| OBSERVE | a GENERATION observation carrying the source fingerprint |

The importer owns the `INVARIANT` kind outright: an id absent from every source file is
retired (mass-retirement guard: more than max(3, 20%) needs `--allow-mass-retirement`);
an id restored in git is reactivated, never duplicated.

## Source contract (schema_version 1)

```yaml
schema_version: 1
domain: example-observability        # must equal the file name stem
owner: example-observability         # default owner for every record
view:
  title: Example Observability invariants
  nav_path: Control Plane / Invariants / Example Observability   # optional
invariants:
  - id: I-EXAMPLE-1                  # I-<UPPER>-...-<n>; unique across ALL domain files
    title: One-line name
    statement: The normative statement.
    ratification: RATIFIED           # PROPOSED | RATIFIED | SUPERSEDED
    enforcement: PARTIAL             # DOCTRINE_ONLY | PARTIAL | ENFORCED
    criticality: IMPORTANT           # NORMAL | IMPORTANT | OPERATIONAL_CRITICAL | POLICY_REQUIRED
    verification_state: UNVERIFIED   # UNVERIFIED | VERIFIED | OUTDATED | EXPIRED
    # VERIFIED requires verified_at (YYYY-MM-DD) and verified_against (the instrument).
    # Optional: owner, must_be_true[], verified_at, verified_against, review_due_at,
    # rationale, established_at, origin, specializes, supersedes[], aliases[],
    # enforced_by[] (MONITOR_RULE alert names), enforcement_refs[] (CI gates, scripts).
```

Ratification and enforcement are separate fields on purpose: "ratified" and
"enforced" are different facts, and one status field is how a record ends up saying
both "ENFORCED" and "implementation pending".

`--validate-only` checks the source with no API access; the source repository runs it
(or an equivalent validator) in CI.

## Anchors

Each record renders under a heading whose text is the bare id (`### I-EXAMPLE-1`), so
the site's own slug is the stable anchor `#i-example-1`. Explicit `{#id}` heading
attributes are deliberately not used: the rendered site does not honour them.

## Operation

Same shape as the meter list: `scripts/run_invariant_catalogue_reconciliation.sh` is
the only entrypoint (flock on `/run/lock/docplane-invariant-catalogue.lock`, a skipped
run exits 0), followed by a read-only status pass that writes
`docplane_generated_projection_*{artifact="invariant-catalogue"}` to the textfile
collector. Units: `config/systemd/docplane-invariant-catalogue.{service,timer}`.

Environment: `DOCPLANE_API`, `INVARIANT_SOURCE_DIR`, optional `INVARIANT_SECTION`
(default `control-plane/invariants`), and the AUTOMATION bearer by file:
`DOCPLANE_INVARIANT_CATALOGUE_TOKEN_FILE` (SECRETS-V3; never the plaintext variable).

Safety: a view path already held by a page the artifact does not own (for example a
hand-authored page) is refused, never replaced and adopted.

Rollback: `systemctl disable --now docplane-invariant-catalogue.timer`. Generated pages
and entities remain; retiring the artifact is a separate, explicit act.
