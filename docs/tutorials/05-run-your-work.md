# 5 · Run your work

The work domain is where new truth is born — and DocPlane shapes it around Getting Things Done, because documentation dies when "I'll write that down later" has nowhere frictionless to land.

## Capture: one sentence, zero decisions

Mid-task, something occurs to you. Save it without breaking flow:

- **Dashboard:** work → Capture box → type → save. Kind defaults to `IDEA`; origin context is stamped automatically.
- **Agent:** `work_capture("switch the backup target to the new disk")` — one MCP call.
- **API:** `POST /api/v1/work/captures` with `{"body": "...", "kind": "IDEA"}`.

Captures land in the **Inbox** untriaged. That's the point: capturing and structuring are different mental modes, and mixing them kills capture.

## Triage: deliberate, later

When you *choose* to (a coffee break, a Friday review), open the inbox and give each capture one of three fates:

- **Promote** — it deserves to be its own initiative.
- **Attach** — it's an activity on an existing initiative.
- **Discard** — guilt-free. A discarded thought cost you one sentence.

## Initiatives: projects with honest states

Initiatives are the GTD projects, with a small state machine:

| State | Meaning |
| --- | --- |
| `ACTIVE` | being worked *now* — soft WIP-limited, so "Now" stays an honest word |
| `BACKLOG` | roadmap: decided, not started |
| `BLOCKED` | waiting on something named |
| `SOAKING` | done, under observation before you trust it |
| `PARKED` | someday/maybe, with a review date |

The work view surfaces the review queues (`parked review due`, `soak review due`, `decisions needed`) so weekly review is a glance, not an excavation. Agents drive the same machine with `work_list`, `work_get`, `work_transition`, `work_note` and `work_link`.

## Closure gates: nothing finishes silently out of sync

Finishing an initiative asks one question per durable domain — this is the loop that keeps the whole corpus honest:

- **know** — were pages or decisions updated?
- **model** — did structure change? cards updated, wires added, something retired?
- **observe** — is monitoring defined or updated for what you built?

Each answer is `UPDATED` (with links to what changed), `NOT_REQUIRED` (with a one-line reason), or `DEFERRED` — and a deferral mints a **visible gap** rather than passing silently. The gate is on the *question*, never on artifact existence: you're never forced to mint an empty runbook to close work. `SOAKING` carries its own gate — soak criteria must reference real monitoring, because what can't be observed can't soak.

Agents read and answer the same gates via `work_dispositions`.

## The habit

- Capture everything, instantly, without judging it.
- Triage in batches, deliberately.
- Keep `ACTIVE` honest — park what you're not actually doing.
- At closure, let the gates ask their three questions; answer honestly, defer visibly.

Do this for two weeks and the work domain becomes the front door of the whole corpus: everything in know, model and observe traces back to a piece of work that put it there.

Next: [Let it observe →](06-let-it-observe.md)
