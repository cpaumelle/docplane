# API-native publication lease — design proposal

*Agent 45, 2026-07-30. Read-only design document — no code in this repo implements
this yet. Written after directly observing why the Git lane file failed: a fast-forward
git push only protects the ref, not the content, so a second committer can overwrite a
"held" claim without ever reading it. That is exactly what happened this session
(`programme/issue-44-shared-record` commit `17f596d`), and separately, two agents made
overlapping (non-colliding, but real) edits to `i-site-transport-authority-1.md` because
nothing stopped them from both believing they could write.*

## Where this lives

`docs-api` already has the right shape for this: `docs.changes` / `docs.reorganisation_plans`
are Postgres tables behind `docs-api`, and `reorganisation_api.py` already defines the
`reorganisation-v1` router. A lease is the same kind of object — a row in the same
database, behind the same API, subject to the same `require_contributor` auth. It should
**not** live in `docs.reorganisation_plans` (that table is for reorganisation
plans/operations, unrelated), but as its own table, e.g. `docs.publication_leases`.

## Schema

```sql
CREATE TABLE docs.publication_leases (
    lease_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    domain              text NOT NULL DEFAULT 'global',   -- mutation domain, see Part 4
    holder_principal_id uuid NOT NULL REFERENCES docplane.principals(principal_id),
    acquired_at         timestamptz NOT NULL DEFAULT now(),
    expires_at          timestamptz NOT NULL,
    last_renewed_at     timestamptz NOT NULL DEFAULT now(),
    base_state_identity text NOT NULL,   -- certification working_state_identity at acquisition
    released_at         timestamptz,     -- null while active
    release_reason      text
);
-- Only one non-released lease per domain, enforced at the DB, not the application:
CREATE UNIQUE INDEX one_active_lease_per_domain
    ON docs.publication_leases (domain)
    WHERE released_at IS NULL;
```

The partial unique index **is** the compare-and-swap. `POST /api/v1/reorganisation/lease`
does `INSERT ... ON CONFLICT (domain) WHERE released_at IS NULL DO NOTHING`, or simply
lets the unique-index violation raise, and returns 409 if a row already occupies that
domain. This is real atomicity — a single Postgres transaction, not "push and hope
nobody else pushed first."

## Endpoints

Following the existing `reorganisation-v1` router's shape (`APIRouter(tags=[...])`,
`Depends(require_contributor)`, `Header(alias="Idempotency-Key")` for mutations):

### `POST /api/v1/reorganisation/lease`

```json
// request
{"domain": "global", "ttl_seconds": 900}
```
```json
// 201 response
{
  "lease_id": "…", "domain": "global", "holder_principal_id": "…",
  "acquired_at": "…", "expires_at": "…", "base_state_identity": "…"
}
```
Requires `Idempotency-Key` (existing convention). Reads `certification/status`'s
`working_state_identity` server-side at acquisition time and stores it as
`base_state_identity` — this is the "acquisition conditioned on current certification/state
identity" requirement, enforced without the caller having to separately assert it.
`ttl_seconds` bounded server-side (e.g. 60–3600) like `DOCPLANE_SELF_ISSUE_TTL_SECONDS`
already is.

**409** if a non-expired, non-released lease already exists for that domain — response
body names the current `holder_principal_id`, `acquired_at`, `expires_at` so the caller
can decide whether to wait or contact a human, exactly like `AUTH_TOKEN_INVALID` etc.
already do with structured `{code, message, remedy}`.

### `GET /api/v1/reorganisation/lease?domain=global`

Returns the active lease for a domain, or `{"active": null}`. No secret values — same
principle `agent_auth.py` already follows (`Principal` never serializes a token). This
is the read-only status surface a Git file used to (badly) approximate; it should now be
authoritative, and the Git file becomes a **mirror**, refreshed by whoever holds the
lease, never the source of truth.

### `POST /api/v1/reorganisation/lease/{lease_id}/renew`

Heartbeat. Requires the caller's principal to be the current `holder_principal_id` (else
403). Extends `expires_at` by another `ttl_seconds` from now, bumps `last_renewed_at`.
Rejects (409) if the lease has already expired and been reaped — the caller must
re-acquire, not resurrect.

### `DELETE /api/v1/reorganisation/lease/{lease_id}`

Explicit release. Sets `released_at`, `release_reason`. Only the holder may release
(else 403) — a human/admin override, if ever needed, goes through the existing
bootstrap-token path (`require_bootstrap_token`), not this endpoint, to avoid adding a
second privileged actor to a table meant to have exactly one writer's worth of authority
at a time.

### Automatic expiry

A lease whose `expires_at` has passed is simply **not** "active" for the purposes of the
unique index and the 409 check — no background reaper process required for correctness
(the partial unique index only excludes `released_at IS NOT NULL`, so a genuinely expired
row still blocks new acquisition unless expiry is also checked). Two implementation
options:
1. Extend the partial index predicate to `WHERE released_at IS NULL AND expires_at > now()`
   — not possible directly (index predicates must be immutable), so instead:
2. `POST /lease` first does `UPDATE ... SET released_at = now(), release_reason = 'expired'
   WHERE domain = $1 AND released_at IS NULL AND expires_at <= now()`, then attempts the
   insert in the same transaction. This makes expiry self-healing at the moment someone
   next tries to acquire, with no separate cron/reaper needed — consistent with this
   codebase's existing preference for synchronous, transactional operations over
   background jobs.

## Publication endpoints require the lease

`create_plan`/`add_operation`/`analyze_plan`/`validate_plan`/`publish_plan` in
`reorganisation_api.py`, and the equivalent `create_change`/`add_operation`/
`validate_change_endpoint`/`publish_change_endpoint` in `agent_api.py`, all gain a new
dependency:

```python
def require_active_lease(
    domain: str = Query(default="global"),
    lease_id: str = Header(..., alias="X-DocPlane-Lease-Id"),
    principal: Principal = Depends(require_contributor),
) -> Lease:
    lease = fetch_active_lease(domain)
    if lease is None:
        raise HTTPException(428, {"code": "LEASE_REQUIRED", "message": "no active lease for this domain"})
    if str(lease.lease_id) != lease_id:
        raise HTTPException(409, {"code": "LEASE_ID_MISMATCH", "message": "the supplied lease_id is not the active lease"})
    if lease.holder_principal_id != principal.principal_id:
        raise HTTPException(403, {"code": "LEASE_HOLDER_MISMATCH", "message": "caller is not the lease holder"})
    if lease.expires_at <= now():
        raise HTTPException(409, {"code": "LEASE_EXPIRED", "message": "lease has expired"})
    current_state = get_certification_state_identity()
    if lease.base_state_identity != current_state:
        raise HTTPException(409, {"code": "LEASE_STATE_DRIFT", "message": "certification state has advanced since lease acquisition", "current_state_identity": current_state})
    return lease
```

This is the **fail-closed matrix** the task asked for, each with a distinct code:

| Condition | Status | Code |
|---|---|---|
| No lease header supplied | 428 | `LEASE_REQUIRED` |
| Lease expired | 409 | `LEASE_EXPIRED` |
| Holder differs from caller | 403 | `LEASE_HOLDER_MISMATCH` |
| Lease ID invalid/not the active one | 409 | `LEASE_ID_MISMATCH` |
| Certification/state identity drifted | 409 | `LEASE_STATE_DRIFT` |
| Another lease already active (at acquisition) | 409 | `LEASE_ALREADY_ACTIVE` |

`analyze_plan`/`validate_plan` (read-modeling, not mutating) and rehearsal-then-abandon
flows can reasonably be exempted from the lease requirement — they don't mutate
committed corpus state. Only `publish_plan` / `publish_change_endpoint` (and, arguably,
`add_operation`/`create_change` once a lease-holding convention is established, to stop
two holders from building competing drafts) must require it. Recommendation: require the
lease starting at `create_change`/`create_plan`, so an entire rehearsal-through-publish
sequence is lease-scoped, matching how this session's actual cohorts were structured.

## Audit trail

Every acquire/renew/release already fits the existing `append_event` pattern
(`event_type="LEASE_ACQUIRED"|"LEASE_RENEWED"|"LEASE_RELEASED"|"LEASE_EXPIRED"`,
`resource_type="publication_lease"`, `resource_id=lease_id`) — no new mechanism needed,
just new event types through the append-only `docs.events` store already used for
`AGENT_CREDENTIAL_SELF_ISSUED`, `CHANGE_CREATED`, etc.

## No secrets in status responses

`GET /api/v1/reorganisation/lease` and the 409 error bodies expose `holder_principal_id`,
timestamps, and `base_state_identity` — none of these are secrets (`Principal` already
never serializes its token; the same discipline applies here by construction, since a
lease row never stores a credential, only a principal ID).

## Git file becomes a mirror, not authority

`handoff/publication-lease.json` (renamed from `publication-lane.json`) is written by
whoever holds the API lease, purely for human/agent readability across the two
coordination surfaces (GitHub for durable evidence, DocPlane for live state) — exactly
the distinction `authority.json` already draws elsewhere. Its schema stays close to
today's for continuity, but every field is now a **copy** of the authoritative
`GET /api/v1/reorganisation/lease` response, and the file must say so:

```json
{
  "_authority_note": "Mirror only. GET /api/v1/reorganisation/lease?domain=global is authoritative. This file may be stale.",
  "lease_id": "…", "domain": "global", "holder": "agent-45",
  "acquired_at": "…", "expires_at": "…", "base_state_identity": "…"
}
```

## Acceptance criteria

1. Two concurrent `POST /api/v1/reorganisation/lease {"domain":"global"}` calls (same or
   different principals) — exactly one returns 201, the other returns 409
   `LEASE_ALREADY_ACTIVE`, even under real concurrency (test with two threads/processes
   racing the same insert, not sequential calls).
2. A `publish_plan`/`publish_change_endpoint` call with no `X-DocPlane-Lease-Id` header
   → 428 `LEASE_REQUIRED`.
3. A publish call with a lease ID belonging to a different, still-active lease → 409
   `LEASE_ID_MISMATCH`.
4. A publish call from a principal that isn't the lease's holder, using a
   *correct* lease_id → 403 `LEASE_HOLDER_MISMATCH`.
5. A publish call after `expires_at` has passed, before anyone re-acquires → 409
   `LEASE_EXPIRED`, and the domain becomes acquirable again on the *next* `POST /lease`
   call without manual intervention.
6. A publish call where `certification/status.working_state_identity` has changed since
   `base_state_identity` was captured → 409 `LEASE_STATE_DRIFT`.
7. `renew` extends `expires_at` only for the true holder; a non-holder's renew attempt →
   403.
8. `DELETE /lease/{id}` from the true holder sets `released_at`; a subsequent `POST
   /lease` for the same domain succeeds immediately.
9. Every acquire/renew/release/expire produces exactly one `docs.events` row, queryable
   the same way `AGENT_CREDENTIAL_SELF_ISSUED` events already are.
10. No lease-related response body (200, 403, 409, 428) contains a bearer token or any
    `docplane.api_tokens` field.
