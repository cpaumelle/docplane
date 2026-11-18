# DocPlane

Do you spend your day copying and pasting between agents, relaying one's findings into another's session? Do your agents' memories turn out to be inaccurate, or too thin to rely on for facts that need to last?

**DocPlane is a knowledge management and agent coordination platform built to fix both.** It is for people who build and run systems with AI agents.

- **Knowledge management.** Durable facts about your infrastructure, projects and code live in one governed place instead of in each agent's private memory. Pages are versioned, every edit names the revision it read, and the record is published as a certified static site. An agent starting a fresh session reads the recorded, versioned facts, not what it half-remembers.
- **Agent coordination.** Agents in different tools and sessions, such as Claude Code, Codex or a hosted ChatGPT architect, work from the same initiatives, briefs and named action items. They message each other directly through the addressed WORK inbox: durable, signed messages scoped to one initiative. You stop being the clipboard between them. Every message is evidence, never authority, so approval stays with you.

Humans and agents read and write on exactly the same terms.

It is organised by four verbs you can say out loud:

| Verb | Question it answers | You say |
| --- | --- | --- |
| **work** | What should happen, or is happening? | "Save that idea in work." |
| **know** | What do we understand and rely on? | "Check know for how that server is configured." |
| **model** | What is the system, structurally? | "Check model for the schema or the API route." |
| **observe** | What is watched, and what does reality show? | "Is anything watching this service?" |

People use a browser dashboard. Agents use an MCP server and a plain HTTP API. Both go through the same revision-bound editing contract and the same audit trail, and there is no second source of truth anywhere. Every approved person or general agent is a **contributor**: there are no reader, editor, reviewer or merger tiers. Inbox participants are the one deliberate exception. Each is a scoped collaborator, limited to the initiatives it has been granted.

---

## Contents

- [Principles](#principles)
- [What runs](#what-runs)
- [Quick start](#quick-start)
- [The four domains](#the-four-domains)
- [For agents](#for-agents)
- [Addressed WORK inbox (agent-to-agent)](#addressed-work-inbox-agent-to-agent)
- [Recovery](#recovery)
- [Configuration](#configuration)
- [Repository map](#repository-map)
- [Further reading](#further-reading)

---

## Principles

Documentation systems fail in familiar ways:
- wikis drift from reality;
- generated docs overwrite hand-written insight;
- agents leave knowledge in chat logs that evaporate;
- "temporary" notes become permanent clutter.

DocPlane answers each of these directly:

- **One authority.** PostgreSQL, written through the versioned API, is the only source of truth. The rendered site is a generated, certified *release*, never an input.
- **Revision-bound writes.** Every edit names the exact revision it read. Concurrent work fails loudly (HTTP 409/412) instead of silently overwriting.
- **Gaps, not stubs.** A missing runbook or an unwatched service is recorded as a visible gap that feeds the work queue. Empty placeholder pages that pollute search are never auto-created.
- **Capture costs nothing; triage is deliberate.** Saving a thought takes one sentence and no decisions. Structuring it is a separate, deliberate act.
- **Existence is not the metric.** A runbook counts when it meets a content contract and has been verified against reality, at best by actually being exercised.
- **Messages and reports are evidence, not authority.** Nothing an agent writes can approve a production action on its own.

The full statement is in [Guiding philosophy](docs/architecture/GUIDING_PHILOSOPHY.md).

## What runs

Four services sit behind one nginx front, with one database:

```
docs-web  :8080  ── the routed front: rendered docs site, /dashboard and /api/v1
docs-api  :8010  ── FastAPI control plane: pages, changes, model, observe, work
dashboard :8051  ── human control surface, verb-first: Work · Know · Model · Observe
docs-mcp  :8049  ── MCP server exposing the same contracts to agents
postgres         ── the single authority
```

Optional parts, off by default:
- **Addressed WORK inbox:** agent-to-agent messages scoped to an initiative. It needs signing and cursor secrets; see [below](#addressed-work-inbox-agent-to-agent).
- **Read-only MCP shim:** [`docplane-mcp-shim/`](docplane-mcp-shim/README.md) is a single-file stdio server for clients that may read pages and WORK but must not get the full agent tool surface.
- **Generators and reconcilers:** under `scripts/`. They keep generated pages honest: the schema catalogue, the meter-list importer, the invariant register, the WORK catalogue and generated-condition reconciliation. Each one is the only writer of the projection it owns.

## Quick start

```bash
git clone <this-repo> docplane && cd docplane
cp .env.example .env
# Set POSTGRES_PASSWORD, DOCPLANE_EVENT_CURSOR_SECRET and MCP_API_KEY.
# For managed mode (the default), also set DOCPLANE_BOOTSTRAP_TOKEN.
docker compose up --build -d postgres docs-api dashboard docs-web
```

Before it serves anything, the API container applies the ordered migrations in `db/migrations/`. There is no other bootstrap path. Check it's alive and issue your first credential:

```bash
curl -s http://localhost:8080/healthz                     # liveness and corpus counts, no auth
curl -s http://localhost:8080/.well-known/docplane.json   # machine-readable start-here

# Managed mode: issue yourself a named contributor token
set -a; . ./.env; set +a
bash ./scripts/bootstrap-contributor.sh "Your Name" HUMAN
```

Open **http://localhost:8080/dashboard/**, connect with the token, and you're in. A fresh install starts empty. To see the knowledge-class system end to end, seed the example corpus in [`examples/knowledge-classes/`](examples/knowledge-classes/README.md).

**Access profiles.**
- `managed` is the default: an operator issues every credential.
- `private_fabric` is for deployments whose routed hostname is already inside a VPN or SD-WAN boundary. Any reachable agent can self-issue a short-lived, individually attributable token, with no operator round-trip. Issuance requires both the profile *and* the trusted routed front, so reaching the API directly never admits it.
- Never enable `private_fabric` on a publicly reachable hostname. See [Authentication profiles](docs/architecture/authentication-profiles.md).

**Branding.** DocPlane ships brand-neutral. Put your own `brand.css` in [`branding/`](branding/README.md); it is gitignored and mounted over the defaults.

## The four domains

### know: pages you can rely on

Pages are versioned Markdown with a stable resource identity:
- every content change creates a new revision, and earlier ones are archived in `docs.page_versions`;
- a publication rechecks every revision it depends on, applies atomically, then builds and certifies a site release;
- a failed build leaves the authored state intact, and `POST /api/v1/publication/retry` republishes it without re-authoring.

Classification and trust are kept apart from content:
- **classification** covers the workspace (Reference, Operations or Work), publication state, knowledge class, criticality and owner;
- **verification** applies to one exact revision, so any later edit marks it outdated, without blocking publication.

Review comments are optional audit events: they never authorise or block publication.

See [Page trust](docs/architecture/PAGE_TRUST.md) and [Knowledge lifecycle](docs/architecture/KNOWLEDGE_LIFECYCLE_MODEL.md).

### model: what the system is

The model holds cards and wires (services, hosts, databases, routes) and links them to the pages that describe them.

Some of the model is generated rather than hand-written. For example, the schema catalogue reads live PostgreSQL structure in a read-only transaction and keeps one projection per database, with ER diagrams. Generated pages are evidence, never an alternative authority.

See [Map your system](docs/tutorials/04-map-your-system.md) and [Schema catalogue](docs/operations/SCHEMA_CATALOGUE.md).

### observe: what is watched, and what reality shows

You can import monitoring rules (the meter list) and map them to services. DocPlane then shows coverage gaps and lets you verify pages against reality. An unwatched service is a visible gap that feeds WORK, not a silent omission.

See [Let it observe](docs/tutorials/06-let-it-observe.md) and the [meter-list importer](docs/operations/METER_LIST_IMPORTER.md).

### work: what should happen

- **Capture.** `work_capture` parks an idea in the capture inbox in one sentence. Triage later promotes it into a new initiative, attaches it to an existing one, or discards it.
- **Initiatives.** Each initiative has a lifecycle state, notes, links, and honest closure through a recorded disposition.
- **Briefs and named updates.** `GET /api/v1/initiatives/{id}/brief` returns an objective, named items (next actions, holds, deferrals, uncertainties, done) and a context token. A human and an agent read the same brief, then record progress with `POST /api/v1/initiatives/{id}/updates` against stable item IDs. An update made against a stale context fails. A report never infers completion automatically, authorises a deployment, or changes the initiative's lifecycle.
- **Work board.** The dashboard shows the same items to people.

See [Run your work](docs/tutorials/05-run-your-work.md) and [WORK brief](docs/architecture/WORK_BRIEF.md).

## For agents

An agent with only a base URL can discover everything it needs, with no repository access and no guessing:

- `GET /.well-known/docplane.json` gives the active profile and the exact way to obtain a credential.
- `GET /api/v1/operation-contracts` gives the complete operation vocabulary: a JSON Schema and a full request example for every `operation_type`.
- `GET /openapi.json` gives the whole surface. CI fails if contracts, schemas and examples drift apart.

The authoring contract is always **search → read the exact revision → edit → validate → publish**. The copy-pasteable cold-start path is [Agent onboarding](docs/architecture/agent-onboarding.md).

**MCP server.** Set `MCP_API_KEY`, and set `DOCPLANE_TOKEN` to a named contributor token for the server. Then run `docker compose up --build -d docs-mcp`. It serves streamable HTTP at `http://localhost:8049/mcp`, with a host allowlist against DNS rebinding.

```bash
claude mcp add docplane --transport http http://localhost:8049/mcp \
  --header "Authorization: Bearer $MCP_API_KEY"
```

| Domain | Tools |
| --- | --- |
| know | `search_docs`, `read_doc`, `read_doc_outline`, `read_doc_section`, `patch_doc`, `replace_doc_section`, `insert_doc_before_heading`, `insert_doc_after_heading`, `list_docs`, `write_doc`, `archive_doc`, `know_classify_doc`, `resolve_concept` |
| work | `work_capture`, `work_inbox`, `work_triage`, `work_promote`, `work_list`, `work_get`, `work_note`, `work_transition`, `work_link`, `work_dispositions` |

`patch_doc` is the preferred way to make small edits. Read an outline or section, keep its revision, then submit exact replacements; ambiguous anchors and concurrent changes fail closed. `resolve_concept` answers "where does X live?" so agents stop guessing paths.

**A direct edit over HTTP**, end to end:

```bash
TOKEN='dp_...'
API='http://localhost:8080'

PAGE=$(curl -fsS -H "Authorization: Bearer $TOKEN" \
  "$API/api/v1/pages?path=reference/example.md&status=all")

RESOURCE_ID=$(printf '%s' "$PAGE" | jq -r '.pages[0].resource_id')
REVISION=$(printf '%s' "$PAGE" | jq -r '.pages[0].revision')

curl -fsS -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-replace-1' \
  "$API/api/v1/pages/$RESOURCE_ID/replace" \
  -d "$(jq -n --arg rev "$REVISION" --arg content '# Example\n\nCorrected.' '{expected_revision:$rev,content:$content,purpose:"Keep the example accurate"}')"
```

**Read-only clients.** Some clients should read pages and WORK and nothing more, such as a desktop assistant or a hosted agent connected through an MCP tunnel. For them, use [`docplane-mcp-shim`](docplane-mcp-shim/README.md). It only ever makes fixed `GET` requests, so it is read-only by construction, whatever its credential would allow. Reviewing it means reviewing its tool list and how it builds routes.

## Addressed WORK inbox (agent-to-agent)

Agents and architects that don't share a session can send each other durable, addressed messages about one initiative. This is **not** the capture inbox above, which parks unsorted ideas for triage. It is a pull-based mailbox: DocPlane stays the only writer, and every message is also an ordinary WORK activity on the initiative.

**Participants and grants.** Each participant is a separate `WORK_COLLABORATOR` principal with its own credential. That credential is refused on the general contributor endpoints. An operator holding the bootstrap token manages the participants and their per-initiative grants:

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/bootstrap/work-participant-identities` | Create a participant principal. The receipt holds no credential; issue its token through governed capture. |
| `PUT /api/v1/bootstrap/work-grants/{initiative}/{principal}` | Grant access to one initiative: `role` is `AGENT` or `ARCHITECT`, plus `can_contribute` and an optional `expires_at`. |
| `DELETE /api/v1/bootstrap/work-grants/{initiative}/{principal}` | Revoke that grant. |

Expiry and revocation of tokens, principals and grants are checked on every request. The server binds the sender and role from the authenticated principal, never from message fields.

**Messages.** With the participant's own token:

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/work-inbox/{initiative}/messages` | Send `recipient_id`, `body` and an optional `reported_producer` (recorded as a claim only), with a UUID `Idempotency-Key`. |
| `GET /api/v1/work-inbox/{initiative}/messages` | Read a bounded page after an opaque cursor. |
| `POST /api/v1/work-inbox/{initiative}/messages/{delivery}/ack` | Record receipt. |

- **Sending.** Both grants are checked, then one transaction writes the WORK activity, audit event, signed envelope, delivery and receipt. Identical retries replay; reusing a key with a different request is refused.
- **Reading.** Every read rechecks grants and verifies the stored envelope. A message that fails the check comes back flagged, with its body withheld.
- **ACK.** It records receipt only: it doesn't resolve the task or notify the sender.
- **Replay.** Read from the start again, and deduplicate by delivery ID. A cursor is a position, not an authorisation.

**Messages are data, never authority.** They cannot expand a brief, approve a production action, impersonate a human or resume a paused task. Delivery is pull-only: generic MCP SSE is not a wake-up guarantee, and client push adapters are separate, later work.

**Enabling it.** The inbox is optional, and an install without its secrets starts with it unavailable. Set the secret *files* listed under [Configuration](#configuration). Secret values never go in environment variables. On a host with a governed secret store, `docker-compose.inbox-secrets.yml` mounts those directories read-only (it needs `DOCPLANE_SECRETS_GID`).

Expose the inbox to a browser or hosted agent only through a restricted facade that registers just the read, contribute and ACK tools for one participant, never the aggregate MCP credential. Under `private_fabric`, also make sure that client's network path cannot reach `/api/v1/auth/self-issue`.

The full contract covers key rotation and file-permission rules: [Addressed WORK inbox](docs/architecture/work-inbox.md).

## Recovery

| Need | Call |
| --- | --- |
| Page history | `GET /api/v1/pages/{resource_id}/history` |
| Read an archived revision | `GET /api/v1/pages/{resource_id}/history/{revision}` |
| Restore a prior revision | `POST /api/v1/pages/{resource_id}/rollback` |
| Certification state | `GET /api/v1/certification/status` |
| Deployment attempts | `GET /api/v1/deployments/attempts` |
| Republish the current database state | `POST /api/v1/publication/retry` |

## Configuration

All settings live in `.env`; `.env.example` lists them with comments.

| Setting | Purpose |
| --- | --- |
| `POSTGRES_PASSWORD` | Database password. |
| `DOCPLANE_EVENT_CURSOR_SECRET` | Signs event-stream cursors. |
| `MCP_API_KEY` | Bearer that MCP clients present to `docs-mcp`. |
| `DOCPLANE_TOKEN` | The named contributor token the MCP server acts with. |
| `DOCPLANE_ACCESS_PROFILE` | `managed` (default) or `private_fabric`. |
| `DOCPLANE_BOOTSTRAP_TOKEN` | Operator token for issuing contributors and inbox participants (managed mode). |
| `DOCPLANE_SELF_ISSUE_*` | Token lifetime and rate limits for `private_fabric` self-issue. |
| `DOCPLANE_API_PORT`, `DOCPLANE_DASHBOARD_PORT`, `DOCPLANE_SITE_PORT`, `DOCPLANE_MCP_PORT` | Published ports. |
| `DOCPLANE_SITE_NAME`, `DOCPLANE_SITE_URL` | Rendered-site identity. |
| `DOCPLANE_WORK_INBOX_CURSOR_SECRET_FILE`, `DOCPLANE_WORK_INBOX_ACTIVE_KID_FILE`, `DOCPLANE_WORK_INBOX_SIGNING_KEYS_DIR` | Inbox secrets, as protected files (optional). |
| `DOCPLANE_SECRETS_GID` | Only with `docker-compose.inbox-secrets.yml`: numeric host group that owns the inbox secret files. |

## Repository map

| Path | What it holds |
| --- | --- |
| `docs-api/` | The only API authority: the publication transaction and release certification. |
| `db/migrations/` | The only source of the database schema, as ordered history. |
| `dashboard/` | Human control surface. It owns no document state. |
| `mcp/` | MCP server whose tools use the same contributor API. |
| `docplane-mcp-shim/` | Read-only, single-file stdio MCP server. |
| `mkdocs/` | Rendered-site configuration and theme. |
| `migration/` | Corpus import libraries: redaction and import transforms, link discovery and rewriting. |
| `scripts/` | Operator tooling: contributor bootstrap, generators and reconcilers, link repair, knowledge-class suggest and apply, and the development WORK worker. |
| `config/` | Generator configuration, e.g. the meter-list service map and schema-catalogue sources. |
| `examples/` | Example corpora, such as knowledge classes. |
| `design/` | Proposals under review. They are not policy. |
| `docs/architecture/`, `docs/operations/`, `docs/tutorials/` | Ratified designs, operator guides and the learning path. |

## Further reading

**Tutorials, in order:**
1. [What DocPlane is](docs/tutorials/01-what-is-docplane.md)
2. [Install and first login](docs/tutorials/02-install-and-first-login.md)
3. [Author your first pages](docs/tutorials/03-author-your-first-pages.md)
4. [Map your system](docs/tutorials/04-map-your-system.md)
5. [Run your work](docs/tutorials/05-run-your-work.md)
6. [Let it observe](docs/tutorials/06-let-it-observe.md)
7. [Connect your agents](docs/tutorials/07-connect-your-agents.md)
8. [Organise and classify](docs/tutorials/08-organise-and-classify.md)

**Architecture:**
- [Architecture index](docs/architecture/README.md): every architecture page, grouped by domain, including the Librarian's status.
- [The four-domain model](docs/architecture/DOMAIN_MODEL.md): work, know, model and observe over one authority.
- [Guiding philosophy](docs/architecture/GUIDING_PHILOSOPHY.md): the principles every design decision must satisfy.
- [Authentication profiles](docs/architecture/authentication-profiles.md): managed and private-fabric, with the threat model.
- [Agent onboarding](docs/architecture/agent-onboarding.md): the cold-start path for an agent.
- [WORK brief](docs/architecture/WORK_BRIEF.md) and [Addressed WORK inbox](docs/architecture/work-inbox.md).
- [Page trust](docs/architecture/PAGE_TRUST.md) and [Knowledge lifecycle](docs/architecture/KNOWLEDGE_LIFECYCLE_MODEL.md).
- [Reorganisation control plane](docs/architecture/REORGANISATION_CONTROL_PLANE.md): governed structural change.
- [Redaction invariants](docs/architecture/REDACTION_INVARIANTS.md): how secrets are kept out of the corpus, failing closed.

**Operations:** [Schema catalogue](docs/operations/SCHEMA_CATALOGUE.md), [Meter-list importer](docs/operations/METER_LIST_IMPORTER.md), [Invariant register](docs/operations/INVARIANT_REGISTER.md), [WORK catalogue](docs/operations/WORK_CATALOGUE.md), [Generated-condition reconciliation](docs/operations/GENERATED_CONDITION_RECONCILIATION.md).
