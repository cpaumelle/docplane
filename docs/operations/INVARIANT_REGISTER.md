# Invariants register generator

The invariants register is know-domain knowledge ([DOMAIN_MODEL.md](../architecture/DOMAIN_MODEL.md),
"The know domain"; implementation plan Sprint 8): standing rules that must hold now, each
with a stable id, a statement, a link to what established it, and a pointer to what
enforces it. An invariant without an enforcement pointer is a wish, and the register
flags it for demotion.

Invariants are **not** model entities. The model domain is the card index of what the
fabric structurally is; the register is a governed know-domain view.

## Authority

The authoritative source is structured YAML in git, one file per domain
(`<domain>.yaml`). Git holds the record history and review trail.
`scripts/invariant_register.py` renders every domain file into **one** generated,
ID-addressable register page and publishes it through the governed change contract as a
named AUTOMATION principal. It follows the generated-artifact pattern of the schema
catalogue, meter list and work catalogue:

| Layer | Written |
|---|---|
| KNOW | one GENERATED register page, default `control-plane/invariants/register.md`, `knowledge_class=POLICY` |
| KNOW | a permanent thin presence page, default `control-plane/invariants/index.md`. It is created only when absent and is hand-curated afterwards. An existing page at that path is left alone. |
| MODEL | one artifact declaration, `invariant-register`, owning the register page. Its source is a `SYSTEM` card, `invariant-registry`, following the work-catalogue precedent. This is provenance only; no invariant is stored in the model. |
| OBSERVE | a GENERATION observation carrying the source fingerprint |

An unchanged source publishes nothing and replays one NOMINAL observation.

## Page shape and anchors

The register has a summary table, then one `## <domain>` section per domain, then one
`### <ID>` heading per invariant. Because each heading's text is the bare ID, the site's
own slug is the stable anchor: `register/#i-obs-liveness-1`. Explicit `{#id}` heading
attributes are not used, because the rendered site does not honour them.

## Source contract (schema_version 1)

```yaml
schema_version: 1
domain: example-observability       # must equal the file name stem
owner: example-observability        # default owner of every record
title: Example Observability invariants
invariants:
  - id: I-EXAMPLE-1                 # i-<topic>-<n> convention; unique across ALL domain files
    title: One-line name
    statement: The normative statement.
    ratification: RATIFIED          # PROPOSED | RATIFIED | SUPERSEDED
    enforcement: PARTIAL            # DOCTRINE_ONLY | PARTIAL | ENFORCED
    criticality: NORMAL             # NORMAL | IMPORTANT | OPERATIONAL_CRITICAL | POLICY_REQUIRED
    verification_state: UNVERIFIED  # UNVERIFIED | VERIFIED | OUTDATED | EXPIRED
    enforced_by: [SomeAlert]        # and/or enforcement_refs: [CI gate, script, code path]
    established_by: operations/decisions/example.md   # establishing ADR/decision page, when one exists
    # Optional: owner, must_be_true[], verified_at, verified_against, review_due_at,
    # rationale, established_at, origin, specializes, supersedes[], aliases[]
```

Rules:
- `VERIFIED` requires both `verified_at` and `verified_against`, the instrument used.
- `PARTIAL` or `ENFORCED` requires at least one enforcement pointer.
- `DOCTRINE_ONLY`, or no pointer at all, renders as a **demotion candidate**. SUPERSEDED records are history, not candidates.
- `established_by` must resolve to a real page, active or archived, before anything is published. The generator fails closed otherwise.

`--validate-only` checks the source with no API access. The source repository runs its own
CI validator for the same contract, plus checks only it can make, such as whether a cited
alert exists.

## Operation

This is the same shape as the meter list:
- **Entrypoint:** `scripts/run_invariant_register_reconciliation.sh` is the only one. It
  holds a flock on `/run/lock/docplane-invariant-register.lock`, and a skipped run exits 0.
- **Status pass:** a read-only pass follows each run and writes
  `docplane_generated_projection_*{artifact="invariant-register"}` to the textfile collector.
- **Units:** `config/systemd/docplane-invariant-register.{service,timer}`.
- **Environment:**
  - `DOCPLANE_API`
  - `INVARIANT_SOURCE_DIR`
  - optional `INVARIANT_REGISTER_PATH` and `INVARIANT_PRESENCE_PATH`
  - the AUTOMATION bearer, by file only: `DOCPLANE_INVARIANT_REGISTER_TOKEN_FILE`

Safety: a page at the register path that the artifact doesn't own is never replaced and
adopted.

Rollback: `systemctl disable --now docplane-invariant-register.timer`. The register page
remains. Retiring the artifact is a separate, explicit act.
