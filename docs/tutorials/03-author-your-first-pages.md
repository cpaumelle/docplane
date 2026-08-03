# 3 · Author your first pages

The best way to learn DocPlane is to start documenting a real system you run — today, in this tutorial. Pick one: your homelab, a side project, the service you're on call for.

## Plan five pages, not fifty

Start with a spine that answers a newcomer's first questions. A shape that works for almost any system:

```
start/index.md              what this system is, and how these docs work
architecture/overview.md    the one-page explanation of how it hangs together
services/<name>/index.md    one page for your most important service
operations/runbooks/…       ONE runbook — for the thing that last broke
decisions/0001-….md         why one non-obvious choice was made
```

Don't scaffold the rest. Empty sections are debt; DocPlane's whole philosophy is *gaps, not stubs* — you'll record the missing pieces as work items instead ([Tutorial 5](05-run-your-work.md)).

## Write the first page in the dashboard

Open **Author** in the dashboard:

1. Search finds nothing yet — so create through your agent or the API below, or seed with the example corpus and edit from there. Once a page exists: search, click a result, and the editor loads it **bound to its exact revision**.
2. Edit the Markdown. The diff panel shows exactly what you're changing.
3. Write one honest sentence in **Purpose** — it becomes part of the audit trail.
4. **Create change → Validate → Publish.** Publication archives the prior version, applies atomically, rebuilds the site and records a certification receipt. Your page is live at `http://localhost:8080/` seconds later.

There is no review gate: every contributor publishes directly, and review comments are optional audit events. History is your safety net, not permission.

## Create pages from the command line

Creation goes through the same governed change contract. The complete copy-pasteable flow (create → publish → verify) is in [Agent onboarding](../architecture/agent-onboarding.md); the short version:

```bash
TOKEN='dp_...'; API='http://localhost:8080'

# 1. Open a change
CHANGE=$(curl -fsS -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: first-page-$(date +%s)" \
  "$API/api/v1/changes" \
  -d '{"title":"First page","purpose":"Start the corpus","workspace_key":"reference"}')
CHANGE_ID=$(printf '%s' "$CHANGE" | jq -r .change_id)

# 2. Add a CREATE_PAGE operation (exact payload schema: GET /api/v1/operation-contracts)
curl -fsS -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: first-page-op" \
  "$API/api/v1/changes/$CHANGE_ID/operations" \
  -d '{"operation_type":"CREATE_PAGE","payload":{"path":"start/index.md","title":"Start here","nav_path":"Start/Start here","content":"# Start here\n\nWhat this system is."}}'

# 3. Validate, then publish
curl -fsS -X POST -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: fp-validate" "$API/api/v1/changes/$CHANGE_ID/validate"
curl -fsS -X POST -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: fp-publish"  "$API/api/v1/changes/$CHANGE_ID/publish"
```

For a small edit, use `POST /api/v1/pages/{resource_id}/patch` with the revision returned by your read and exact `old_text`/`new_text` edits. The server rejects stale revisions, ambiguous anchors, overlaps and no-ops, while recording the same full revision and audit trail as every other publication. Use `/replace` only when the complete page really is the intended unit of change. Agents can use `read_doc_outline`, `read_doc_section` and `patch_doc` ([Tutorial 7](07-connect-your-agents.md)).

## Give every page a knowledge class

Each know page carries a `knowledge_class` — its *mode of use*, not its topic:

| Class | It is… | Lifecycle |
| --- | --- | --- |
| `ARCHITECTURE` | an explanation of how something hangs together | few pages, rewritten freely, kept current |
| `OPERATION` | a runbook: preconditions, commands, checks, rollback | born from real events, verified by being exercised |
| `REFERENCE` | facts you look up | often a candidate for generation |
| `POLICY` | a standing rule people must follow | small, enforced |
| `DECISION` | a point-in-time choice with context and consequences | immutable; superseded, never edited |
| `EVIDENCE` | a record of what happened (incident, audit, snapshot) | append-only ledgers |
| `DESIGN` | a proposal or spec on its way to a decision | graduates or archives |
| `WORK_NOTE` | work-domain state wearing a page | belongs to work, not know |

Classify at birth — `write_doc` and the API accept `knowledge_class` on creation, and the dashboard's class chip records a governed reclassification any time after. Classes power navigation, review queues and freshness policy later, so the habit pays compound interest.

## Two habits that keep a corpus honest

- **Purpose every change.** One sentence, always. Future-you reading `Changes & versions` will thank present-you.
- **Runbooks only from reality.** Write a runbook the second time you fix the same thing, from the transcript of what actually worked — never speculatively.

Next: [Map your system →](04-map-your-system.md)
