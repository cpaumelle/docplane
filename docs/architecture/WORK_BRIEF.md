# WORK brief and update: first vertical slice

## Outcome and boundary

One initiative can carry a compact, explicit set of actions and holds. A human
and an agent read the same brief and record progress against the same stable
item IDs. Daily operation uses ordinary Python/PostgreSQL and no model calls,
embeddings, inference services, schedulers or external network verification.

This uses existing WORK authority, authentication, activities and mutation
receipts. It does not import the historical inbox, alter initiative lifecycle,
authorise deployments, or automatically infer completion from a report.

## Contract

- `GET /api/v1/initiatives/{id}/brief` returns `work-brief-v1`: objective,
  scope, initiative state, named items, next-action/held/deferred/uncertain/done
  groups, a context token and five recent activities. Group entries are item IDs,
  so an agent does not receive every item's text twice.
- `POST /api/v1/initiatives/{id}/updates` takes a canonical UUID
  `Idempotency-Key`, the brief's `expected_context`, a summary, self-reported
  producer, and one to ten named item changes. OpenAPI publishes its full schema.
- Items keep a stable `item_id`, title, state, posture, reason, evidence,
  optional owner, and a concrete next action for active open work. Existing
  Librarian obligation IDs may be retained when deliberately entered; no old
  bundle is ingested or modified automatically.
- State is `OPEN`, `DONE_IN_RECORD`, `UNCLEAR`, `SUPERSEDED` or `WITHDRAWN`.
  Posture is independently `ACTIVE`, `HELD`, `DEFERRED` or `UNKNOWN`.
  This preserves the distinction the supervised reviews found missing.
- An omitted item is unchanged. No deletion operation exists. A hold/deferral
  cannot be released, completed or have its decision basis replaced without an
  explicit decision with a reason and resolvable WORK activity/capture or page
  reference. Reopening recorded completed work needs the same decision record.
- Typed references identify evidence. Local activity/capture/page identities are
  existence-checked. Content, external PRs, commits, artifacts and host state are
  still recorded claims, not independently verified truth or execution grants.
  Canonical secret refusal applies to all authored text and references. A long
  bare hash may be refused under the existing redaction policy; this change does
  not weaken that policy. The server-generated context hash is a dedicated typed
  field, excluded from authored-text scanning.

A current brief is needed before each logical update. Its token binds initiative
version and activity count/latest identity. Ordinary notes invalidate the token
without changing actions automatically. A stale update returns
`WORK_CONTEXT_STALE`; read, rebase and use a new key. An identical retry returns
its original receipt even if the brief has since changed. Changed intent with
an old key is refused. The dashboard preserves the original key/payload when a
request's outcome is unknown.

## Persistence and concurrency

There is no migration or second work database. Each update appends one NOTE
containing a readable report plus a typed `work_checkpoint` in the existing
activity metadata. The checkpoint merges named changes with the previous
snapshot, preserves untouched items, and records the exact changes and any
hold decisions. Older checkpoints and filing receipts remain immutable.

Checkpoint writes lock the initiative, increment its existing version, and append
an event and replay receipt in the same transaction. The latest checkpoint is
selected by that monotonically increasing revision, not a transaction-start
clock. Normal note/attach paths use the same initiative lock. The brief also
reports how many activities arrived after its checkpoint, so later evidence is
visible rather than silently treated as reconciled.

An initiative may carry at most 50 checkpoint items and 64 KiB of item JSON;
updates allow ten changes and 32 KiB of request JSON, with a readable activity
body capped at 20 KiB. Limits refuse the whole update rather than truncate or
lose work. The first slice does not compact completed items automatically.
Unsupported/malformed checkpoints fail closed. Withheld checkpoints are shown
as unknown, never as an empty todo list.

## Human surface and agent rollout

The dashboard opens on the Work board. Choose an initiative, read its purpose,
next actions, holds, deferrals and recent evidence, then use a labelled form to
add or update one item. Full text is preserved; the form is not a JSON editor.
The inbox remains available separately for genuinely new concerns.

Initially existing initiatives show `NOT_SET`: history has not been interpreted.
Pilot on one reviewed initiative. Read its objective/scope and decision history,
then deliberately record the starting actions and holds. Do not infer an empty
backlog from the absence of a checkpoint. No production fixtures are installed.

The canonical consolidated MCP requires its companion adapter change exposing
`docplane_work_brief` and `docplane_work_update` with the existing WORK-evidence
identity. Deploy the API first. Do not substitute the loopback bundled MCP or
self-issued credentials when the consolidated tools are unavailable.

## Deployment and acceptance

No DB migration, importer version change, generation, publication or timer is
required. Follow the owning deployment runbook: back up the database, preserve
outgoing API **and dashboard** image tags, verify the existing migration ledger,
fast-forward to the merged, CI-green exact commit, and rebuild/recreate only
`docs-api` and `dashboard` using the existing production Compose overlay.
Preserve PostgreSQL, named volumes, `.env`, site publication and unrelated services.

Before production, exercise the synthetic transaction tests on disposable
PostgreSQL and accept the UI on VM8555. Then verify health/provenance at the merged
SHA, dashboard and discovery, unchanged migration ledger, no model dependency,
a brief on an existing initiative, and the reviewed pilot update/replay/hold case.
Writing the pilot starting state is a deliberate governed WORK mutation, separate
from installing code. Record receipts; do not repeat a write to test idempotency
with a new key.

Rollback restores both preserved application images through the existing Compose
model. The additive checkpoint records remain in PostgreSQL as readable NOTE
activities; old clients ignore their new metadata. Do not restore the database
or delete evidence merely to roll back application code.

## Regression evidence

The reviewed examples drive tests for preserved held/deferred work, no next-action
nudge on held/unclear/parked work, no silent reopening, stable IDs, attributable
updates, invalid evidence, stale context, exact retries and concurrent writers.
They do not claim to solve semantic contradictions or classify old captures.
