# 7 · Connect your agents

DocPlane is agent-native: agents get the same contracts as humans — named identity, revision-bound writes, full audit — through two doors: the MCP server (tools) and the plain HTTP API (everything).

## Start the MCP server

```bash
# .env: set MCP_API_KEY, and DOCPLANE_TOKEN to a named contributor token for the server
docker compose up --build -d docs-mcp
```

The server listens on `http://localhost:8049/mcp` (streamable HTTP), authenticated by `MCP_API_KEY`, with DNS-rebinding protection via a host allowlist that follows your published port automatically. A Claude Code registration looks like:

```bash
claude mcp add docplane --transport http http://localhost:8049/mcp \
  --header "Authorization: Bearer $MCP_API_KEY"
```

### The tools

| Domain | Tools |
| --- | --- |
| know | `search_docs`, `read_doc`, `read_doc_outline`, `read_doc_section`, `patch_doc`, `list_docs`, `write_doc`, `archive_doc`, `know_classify_doc`, `resolve_concept` |
| work | `work_capture`, `work_inbox`, `work_triage`, `work_promote`, `work_list`, `work_get`, `work_note`, `work_transition`, `work_link`, `work_dispositions` |

`patch_doc` is the preferred small-edit path: read an outline or section, retain its revision, then submit exact replacements. Ambiguous anchors and concurrent updates fail closed. `write_doc` creates pages or deliberately replaces a complete page; replacing an existing page requires the revision from the caller's earlier read. Both use the governed change → operation → validate → publish flow. `resolve_concept` answers "where does X live?" so agents stop guessing paths.

## Or go straight to HTTP

An agent with only a base URL can bootstrap itself completely — this is a design guarantee, not luck:

```bash
BASE='http://localhost:8080'
curl -fsS "$BASE/.well-known/docplane.json"     # 1. who am I talking to, how do I get credentials
curl -fsS "$BASE/openapi.json" | jq '.paths|keys|length'   # 2. the full surface
curl -fsS "$BASE/api/v1/operation-contracts"    # 3. exact JSON Schema + example for every operation_type
```

Credentials depend on the deployment profile ([Tutorial 2](02-install-and-first-login.md)):

- **managed** — an operator runs `scripts/bootstrap-contributor.sh "Agent Name" AGENT` and hands over the token.
- **private_fabric** — the agent self-issues through the routed front, no secret required:

```bash
TOKEN=$(curl -fsS -X POST -H 'Content-Type: application/json' \
  "$BASE/api/v1/auth/self-issue" -d '{"display_name":"my-agent"}' | jq -r .token)
```

Tokens are short-lived and individually attributable — every publication names its author in the audit trail, human or not.

## The contract agents must follow

Put this in your agent's system prompt or skill; it is the entire etiquette:

1. **Discover, don't guess.** Start from `/.well-known/docplane.json` and `/api/v1/operation-contracts`. Never infer payload shapes from source code.
2. **Read the exact revision before editing.** Bind every write to it (`expected_revision` / `If-Match`). A 409/412 means someone got there first: refetch, re-derive, retry — never force.
3. **State a purpose** on every change. One sentence of *why*.
4. **Idempotency keys on every mutation** — retries must be safe.
5. **Gaps, not stubs.** If something's missing, `work_capture` it. Never publish an empty page to mark a TODO.
6. **Classify at birth** — pass `knowledge_class` when creating pages.
7. **Verify against reality, bind evidence to revisions.** When asked to verify a page, record what was actually checked.
8. **One task at a time — park everything else.** Mid-task discoveries (a bug, an improvement, a question) get ONE `work_capture` call with `kind` and `context`, then you return to the task. Never chase a discovery, never widen scope unprompted. See [the distraction ledger](05-run-your-work.md#the-distraction-ledger-how-agents-stay-on-task).

The complete worked cold-start — discovery through create, publish, verify, archive and cleanup — is [Agent onboarding](../architecture/agent-onboarding.md).

## Division of labour that works

Agents are excellent at: harvesting cards from configs, drafting runbooks from session transcripts of real fixes, proposing knowledge classes, executing verification requests, repairing links after moves. Humans stay decisive at: approving classifications, disambiguating authority ("which of these two pages is true?"), triaging work, and closure-gate honesty. DocPlane's surfaces are built around exactly that split — the dashboard for the judgement calls, the API for the labour.

Next: [Organise and classify →](08-organise-and-classify.md)
