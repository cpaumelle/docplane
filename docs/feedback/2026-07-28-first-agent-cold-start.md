# First-agent cold-start review of DocPlane

**Author:** claude-code (hub2 session, 2026-07-28) · principal `cb86c42b` (AGENT)
**Build tested:** `9ff86b3`, built 2026-07-27T23:15:53Z · 508 active pages / 429 archived
**Outcome:** cold start → published a real change successfully. Everything below is from doing it, not reading about it.

The goal this feeds: **discovery of the correct method must be easy and unambiguous and must not get in the way, without losing auditability.** The legacy bar is <https://docs.charliehub.net/agent-guides/docs-api/>.

---

## 1. The cold-start trace — what it actually took

This is the most useful evidence in the document. Steps marked ❌ are dead ends a fresh agent hits.

| # | What I tried | Result |
|---|---|---|
| 1 | `GET /api/health` | ❌ 404 — legacy habit |
| 2 | `GET /api/agent-config` | ❌ 404 — **this is step 1 of the legacy guide** |
| 3 | `GET /openapi.json` (guessed) | ✅ 47 paths |
| 4 | `GET /healthz` | ✅ — only found via openapi |
| 5 | `GET /.well-known/docplane.json` | ✅ excellent — but only found via openapi |
| 6 | `GET /api/v1/capabilities` | ❌ 401. Says a token is required, **not how to get one** |
| 7 | Read `README.md` **in the repo** | Found `DOCPLANE_BOOTSTRAP_TOKEN` + `scripts/bootstrap-contributor.sh` |
| 8 | Read `.env` **on the host** | Got the bootstrap token |
| 9 | `POST /api/v1/bootstrap/principals` | ✅ AGENT token issued |
| 10 | Read `docs-api/app/agent_models.py` **in the repo** | Only place the 12 valid `operation_type` values exist |
| 11 | change → operation → validate → publish | ✅ worked first time, clean |

**Steps 7, 8 and 10 required filesystem access to the host and the repo.** An agent on another fabric node with only HTTP cannot get past step 6. DocPlane's own `AGENT_INTERFACE.md` says "DocPlane is endpoint-first" — the design intent is right, the endpoints just don't carry enough yet to honour it.

Legacy's equivalent was two curls with no auth, no `.env`, no SSH, from any fabric node.

---

## 2. Findings by severity

### 🔴 S1 — A stale-revision write is silently discarded and reported as success

**The most important finding for a corpus edited many times a day by many agents.**

`evaluate_change()` builds per-operation errors in `op_errors`, and rolls them into the change-level `errors` at the end of the loop (`publication.py:366`). But three checks `continue` *before* that rollup:

- `OPERATION_UNSUPPORTED` (`publication.py:219–221`)
- `PAGE_NOT_FOUND` (`:225`)
- `PAGE_REVISION_STALE` (`:227–232`)

Since `passed = not errors` (`:403`) only sees change-level errors, `passed` stays `True`. `publish_change()` gates solely on `if not evaluation["passed"]` (`:508`), so it proceeds — and because the operation `continue`d, it never mutated the candidate, so the edit is dropped.

**Reproduced end to end** on a throwaway page (`reference/_agent-probe-20260728.md`, since archived):

```
op receipt : [{'code': 'PAGE_REVISION_STALE',
               'current':  'd898a3e1-…',
               'expected': '00000000-…'}]
validate   : status=VALIDATED  summary.passed=True  summary.errors=[]
publish    : HTTP 200  status=PUBLISHED  operations_applied=1
page after : version 1, ORIGINAL_MARKER intact, STALE_WRITE_MARKER absent
```

The write vanished. The API said `PUBLISHED` and `operations_applied: 1`.

**What is right:** it does not clobber newer work — the "never silently overwrite" half of the contract holds.
**What is wrong:** the losing agent gets a green receipt and never learns. With many agents editing daily, the second writer on any page loses its work silently. That is data loss behind a success signal.

**Fix:** move the rollup so early-exit paths also `errors.extend(op_errors)` — or compute `passed` from `all(not op["errors"] for op in results) and not errors`. Then `publish` correctly 409s with `CHANGE_VALIDATION_FAILED`.

**Also:** `operations_applied` should count operations *applied*, not operations *submitted*.

### 🔴 S2 — Import mutated content without changing revision identity

`services/occupancy-pipeline-hub2/client-validation.md`:

| | legacy | DocPlane |
|---|---|---|
| version | 7 | 7 |
| revision | `00c3491e-5049-450e-be11-382a4c908ef6` | `00c3491e-5049-450e-be11-382a4c908ef6` |
| content | 14,937 chars | 14,977 chars |

Same version, **same revision UUID, different bytes.** The import applied secret redaction:

```diff
-csrfToken = customer-occupancy-service
+csrfToken = <REDACTED:ENV_SECRET_ASSIGNMENT:FAMILY-0043>
-password  = {{password}}
+password  = {<REDACTED:PASSWORD:FAMILY-0016>}}
```

A revision identifier that does not change when content changes is not an identifier. Every guarantee built on `expected_revision` inherits the weakness, and anything that diffed the two corpora to verify the migration would report a false match on version+revision.

**Fix:** any import-time transformation must mint a new revision, and should appear in `history` as its own version with an attributable actor (`migration`/`redactor`), so the change is auditable rather than invisible.

### 🟠 S3 — Redaction has false positives and one malformed rewrite

44 markers across 28 pages. Sampled:

- `customer-occupancy-service` is a **service name**, not a credential.
- `{{password}}` is a **placeholder**, not a secret.
- The placeholder rewrite is brace-unbalanced: `{{password}}` → `{<REDACTED:…>}}` — one `{` consumed. 1 occurrence corpus-wide, so the bug is rare, not systemic.

Net effect: a documented, copy-pasteable curl example in an operational page is now wrong. Redacting real secrets is right; redacting placeholders damages docs. Needs a placeholder allow-list (`{{…}}`, `<…>`, `$VAR`, `changeme`) and a balanced-token rewrite.

### 🟠 S4 — No HTTP-discoverable path from "no credential" to "credential"

The only issuance path needs `DOCPLANE_BOOTSTRAP_TOKEN` from `.env` **on the host**. For a fleet of agents on many nodes this doesn't scale, and it is the single biggest regression against legacy, whose `/api/agent-config` was unauthenticated and gated by network position (the IMDS model).

This is a **policy** decision, not just an endpoint: either adopt legacy's network-position gate for issuance, or state plainly in `.well-known` that tokens are human-issued and how to request one. Right now the 401 is a dead end.

### 🟡 S5 — The machine contract under-describes itself

- `operation_type` is a free-form `string` with **no enum**. The 12 valid values live only in `agent_models.py`. An agent generating a client from the spec cannot know them.
- `securitySchemes` is **empty** — auth is absent from the machine-readable contract.
- `Idempotency-Key` is **required** on `POST …/operations` (428 `IDEMPOTENCY_KEY_REQUIRED`) but is not marked required in the spec.

### 🟡 S6 — Error bodies state the rule, not the remedy

`{"code":"IDEMPOTENCY_KEY_REQUIRED"}` — no `message`, no hint. `AUTH_REQUIRED` has a message but no "get one here". Legacy shipped an error-reference table mapping every status to cause *and* fix; that table is a large part of why agents succeeded on it.

Suggest every error carry `code`, `message`, `remedy`, `docs_url`. For `PAGE_REVISION_STALE` the remedy is mechanical and should be stated: *re-read the page, rebase your edit on the returned `current` revision, resubmit.* The `current` value is already in the payload — say what to do with it.

### 🟡 S7 — Two different "page count" numbers, neither labelled

`/healthz` reports `pages: 937`; the publish receipt reports `pages: 508`. Both true: 937 = 508 active + 429 archived. As the headline number on a health endpoint, 937 reads as corpus size when the site is 508.

### 🟡 S8 — `count` is the page-of-results size, not a total

`/api/v1/pages?limit=1` → `count: 1`. There is no way to learn the corpus total without fetching everything. Either make `count` the total or add `total`.

### ⚪ S9 — Smaller items

- `scripts/bootstrap-contributor.sh` defaults `API_URL=http://localhost:8010`; this deployment publishes `18010` (`DOCPLANE_API_PORT`). The documented command fails as written.
- Tokens are issued with `expires_at: null`. The API accepts `expires_at`; the script never passes it. A non-expiring credential for an ephemeral agent session is the wrong default.
- No endpoint abandons a change. `ABANDONED` exists in code; there is no route. I left two probe changes stranded in DRAFT/VALIDATED with no API way to clean them up.
- `/api/v1/search` returns empty `snippet` fields, so results can't be triaged without a second fetch per hit.

---

## 3. What is genuinely better than legacy — keep all of it

Said plainly because the recommendations below must not cost any of this:

- **Publication is real and observable.** A publish returns a receipt with `release_id`, `sealed_digest`, `deployment_cycle_id`, `build_time_ms`, `operations_applied`, `database_transaction: COMMITTED`, plus `certification: CURRENT` with matching `working_state_identity` / `deployed_state_identity`. Legacy's equivalent is a background worker that has been failing silently every 30 s for days while returning 200 to every write.
- **It actually renders.** My change was live on the site immediately. Legacy's corpus has been frozen since 2026-07-25.
- **Exact-revision binding is the right concurrency model** for many agents — strictly better than legacy's `If-Match: *` last-writer-wins upsert. S1 is a bug in the enforcement, not a flaw in the design.
- **History migrated intact** — v1–v8 with original authors and timestamps, and the superseding `change_id` recorded on the version it replaced.
- **`.well-known/docplane.json`** is the right idea and states it returns no credentials.
- **Validation binds state properly** — `base_state_identity` and `candidate_identity` on every evaluation.

---

## 4. Recommendations — agent-friendly without losing auditability

The tension is only apparent. Auditability lives in *what gets recorded*; friction lives in *how many calls it takes to record it*. Decouple them.

### R1 — Make `.well-known/docplane.json` the single unambiguous entry point

One unauthenticated document that answers everything a cold agent needs, so no agent ever reads the repo again:

- the workflow, in order, with the exact call sequence
- the full `operation_type` vocabulary and which ones need `expected_revision` / `expected_section_hash`
- required headers, including `Idempotency-Key`
- how to obtain a token, or explicitly that it is human-issued
- the error catalogue with remedies
- a worked end-to-end example

Legacy's guide worked because everything was in one place and the first call was free. Reproduce that property here.

### R2 — Add a one-call convenience for the common case, with identical audit output

The 4-call dance (change → operation → validate → publish), plus 2 reads to get `resource_id` and `revision`, is 6 round trips for "fix this page". Offer:

```
POST /api/v1/pages/{resource_id}/replace
     { expected_revision, content, title?, purpose }
```

Internally: create change → add op → validate → publish, atomically. Same change record, same receipt, same certification, same history entry. **Zero auditability lost — the audit substrate is unchanged, only the client's call count drops.** Keep the explicit multi-step path for multi-page and reorganisation work, where it earns its cost.

Consider accepting `path` in place of `resource_id` for this endpoint so a trivial edit needs no lookup at all.

### R3 — Fix S1, then make the conflict path self-healing

Once errors roll up correctly, a stale write 409s. Then make the error teach: return `current_revision` **and** the current content hash, so a client can rebase without a second fetch. With many daily writers this path will be hit constantly — it should be a routine, well-lit retry, not a mystery.

### R4 — Make transformations visible

No pipeline should mutate content without minting a revision and writing a history entry. If the redactor changes a byte, that is an edit and belongs in the audit trail with an actor. This is the same principle the rest of the product already gets right.

### R5 — Complete the machine contract

`operation_type` enum, `securitySchemes`, required headers. An agent that can generate a correct client from `/openapi.json` alone needs no guide for mechanics — which frees the guide to explain judgement instead.

---

## 5. Corrections to my own analysis

Recorded because a first-agent report is only useful if its errors are visible too.

1. I initially concluded **"history did not migrate — 0 entries"**. Wrong. I parsed for `revisions`/`history`; the actual key is `versions`, alongside `current`. History is fully present. *(Worth noting the shape differs from legacy's flat list — a small migration trap for anyone porting a client.)*
2. I initially framed the redaction as **"silent rewrite with no provenance"**. The sharper and more accurate finding is S2: content changed while the **revision identity stayed the same**. That is the defect; "no provenance" was vague.

---

## 6. Work done during this review

- Published `operations/occupancy-hub2-switchoff.md` **v8 → v9** (change `8b8c5b03`, release `2f228321`) — carried across the legacy write the import predated. Verified byte-identical to legacy v9 and live on the rendered site.
- Verified 7 of 9 migrated pages are byte-identical to legacy; 1 needed the update above; 1 differs only by the S2 redaction.
- Created and archived `reference/_agent-probe-20260728.md` to prove S1 without touching a real page.

**Left behind for cleanup:** two probe changes stranded with no abandon endpoint (S9) — `8b8c5b03` is the legitimate published one; the stale-revision probes are the strays.
