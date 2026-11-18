# WORK briefs and named updates

A **brief** is a compact, current view of one initiative: its objective, scope
and a short list of named items (next actions, holds, deferrals, open questions
and recorded completions). A human and an agent read the same brief and record
progress against the same stable item IDs. This is how agents in different
tools and sessions pick up where the last one left off without a person relaying
the context.

Briefs are deterministic. They use ordinary Python and PostgreSQL. No model,
embedding, inference service, scheduler or external check is involved.

A brief does not change an initiative's lifecycle state, authorise a deployment,
or infer that work is complete from a report. Items record claims with evidence;
they are not live verification.

Code: `docs-api/app/work_brief.py` (item model, merge and grouping) and
`docs-api/app/work_brief_api.py` (routes).

## Reading a brief

`GET /api/v1/initiatives/{id}/brief` returns the `work-brief-v1` contract:

- the initiative's identity, state, objective and scope;
- `items`, each with a stable `item_id`, title, `state`, `stance`, reason,
  evidence references, an optional owner and a `next_action`;
- groups that list item IDs (not full items, so text is not sent twice):
  `next_actions`, `held`, `deferred`, `uncertain` and `recorded_done`;
- `execution_eligible` and `paused_actions` (see below);
- a `context_token` that binds the next update to this read;
- the five most recent activities, `recent_activity_has_more`, and
  `activities_since_checkpoint`, so evidence added after the last update is
  visible rather than silently treated as reconciled.

`checkpoint_status` is `NOT_SET` when no update has ever been recorded. That
means the history has not been organised into items. It does **not** mean there
is no work left.

## Item state and stance

Two independent fields describe an item:

- **state**: `OPEN`, `DONE_IN_RECORD`, `UNCLEAR`, `SUPERSEDED` or `WITHDRAWN`;
- **stance**: `ACTIVE`, `HELD`, `DEFERRED` or `UNKNOWN`.

Keeping them apart lets a brief say "still open, but on hold" without losing
either fact.

Grouping is explicit and never based on similarity or recency:

- `DONE_IN_RECORD`, `SUPERSEDED` and `WITHDRAWN` items are listed as recorded
  done;
- otherwise `HELD` and `DEFERRED` items go to their own groups;
- `UNCLEAR` items, and items with stance `UNKNOWN`, are uncertain;
- everything else is a next action.

An item never overrides its initiative's state. Next actions are offered only
when the initiative is `ACTIVE` or `BACKLOG` (`execution_eligible: true`). For a
parked, blocked or closed initiative, open active items appear under
`paused_actions` instead.

An `OPEN` item with stance `ACTIVE` must have a concrete `next_action`.

## Recording an update

`POST /api/v1/initiatives/{id}/updates` takes:

- an `Idempotency-Key` header holding a canonical UUID;
- `expected_context`: the `context_token` from the brief just read;
- a `summary` and a `reported_producer` (who or what says it produced the work,
  recorded as a claim);
- one to ten `changes`, each naming one item and, where needed, a `decision`.

Rules the server enforces:

- **Omitted items are unchanged.** There is no delete operation.
- **Held, deferred and finished items are protected.** Changing an item whose
  stance is `HELD` or `DEFERRED`, or whose state is terminal, needs a decision
  with a reason and evidence. Without it the update fails with
  `WORK_DECISION_REQUIRED`. This covers releasing a hold, completing held work
  and reopening recorded completion. The decision must cite at least one local
  record (an `ACTIVITY`, `CAPTURE` or `PAGE`), otherwise
  `WORK_DECISION_SOURCE_REQUIRED`.
- **Evidence is typed.** Reference types are `ACTIVITY`, `CAPTURE`, `PR`,
  `COMMIT`, `ARTIFACT`, `PAGE` and `CONDITION`. Local activities, captures and
  pages (by path) are checked for existence (`WORK_EVIDENCE_NOT_FOUND`). Pull
  requests, commits, artifacts and host state are recorded claims only.
- **Secrets are refused.** All authored text passes the canonical redaction
  policy; text it would change is refused (`WORK_UPDATE_TEXT_REFUSED`). The
  server-generated context token is a typed field and is not scanned as text.
- **Closed initiatives refuse updates** (`WORK_INITIATIVE_CLOSED`).

### Concurrency and retries

The context token binds the initiative version and its activity count and latest
activity. Any later note or update makes it stale. A stale update fails with
`WORK_CONTEXT_STALE`: read the brief again, rebase the change, and use a new
idempotency key.

An identical retry with the same key returns its original receipt, even if the
brief has changed since. Reusing a key with a different request is refused. The
dashboard keeps the original key and payload when a request's outcome is
unknown, so a retry recovers the same request.

## Storage

There is no separate work database and no migration for briefs. Each update
appends one `NOTE` activity to the initiative. The note holds a readable report
and a typed `work_checkpoint` in the activity metadata. The checkpoint merges the
named changes into the previous snapshot and records the exact changes and any
decisions. Older checkpoints remain as immutable history.

The write locks the initiative, increments its version, and appends an event
(`WORK_CHECKPOINT_RECORDED`) and a replay receipt in the same transaction. The
latest checkpoint is chosen by its revision number, not by a clock.

Limits refuse the whole update rather than truncate it:

| Limit | Value |
| --- | --- |
| Items per initiative | 50 |
| Item JSON per initiative | 64 KiB |
| Changes per update | 10 |
| Update request JSON | about 32 KB |
| Readable activity body | about 20 KB |

Completed items are not compacted automatically. A malformed or unsupported
checkpoint fails closed. A checkpoint that cannot be shown safely is reported as
withheld, never as an empty to-do list.

## Clients

- **HTTP** is the complete contract; OpenAPI publishes the full update schema.
- **Dashboard.** The Work board shows the brief and offers a labelled form to add
  or update one item. Notes do not change items or release holds.
- **Read-only shim.** [`docplane-mcp-shim`](../../docplane-mcp-shim/README.md)
  exposes `work_brief` with the context token removed, because it has no update
  tool.
- **Bundled MCP server.** `mcp/tools/work.py` does not expose brief or update
  tools today. A deployment that wants agents to update briefs through MCP needs
  its own adapter over these two routes.

## Adopting briefs on an existing initiative

Existing initiatives start as `NOT_SET`. Read the objective, scope and decision
history first, then deliberately record the starting actions and holds. Do not
infer an empty backlog from a missing checkpoint.

## Regression evidence

`docs-api/tests/test_work_brief.py` and `test_work_brief_e2e.py` cover preserved held and deferred work, no next-action nudge
on held, unclear or paused work, no silent reopening, stable IDs, attributable
updates, invalid evidence, stale context, exact retries and concurrent writers.
These tests do not claim to resolve contradictions between items or to classify
old captures; that is the [Librarian](LIBRARIAN.md)'s design problem.
