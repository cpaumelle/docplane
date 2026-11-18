# DocPlane

**DocPlane is a documentation control plane for people who build and run systems with AI agents.** It gives your infrastructure, your projects and your code one governed home for everything you and your agents know — authored in PostgreSQL through a versioned API, published as a certified static site, and organised by four verbs you can say out loud:

| Verb | Question it answers | You say |
| --- | --- | --- |
| **work** | What should happen, or is happening? | "Save that idea in work." |
| **know** | What do we understand and rely on? | "Check know for how that server is configured." |
| **model** | What is the system, structurally? | "Check model for the schema or the API route." |
| **observe** | What is watched, and what does reality show? | "Is anything watching this service?" |

One authority, two audiences: every page, card, capture and observation is equally readable and writable by humans (a browser dashboard) and agents (an MCP server and a plain HTTP API) — the same revision-bound editing contract, the same audit trail, no second source of truth anywhere. Every approved identity is a **contributor**; there are no reader/editor/reviewer/merger tiers.

## Why it exists

Documentation systems fail in familiar ways: wikis drift from reality, generated docs overwrite hand-written insight, agents paste knowledge into chat logs that evaporate, and "temporary" notes become permanent landfill. DocPlane's design answers each failure directly:

- **One authority, additive evolution.** PostgreSQL through the versioned API is the only source of truth. The rendered site is a generated, certified *release* — never an input.
- **Revision-bound writes.** Every edit binds to the exact revision it read. Concurrent work conflicts loudly (HTTP 409/412) instead of silently clobbering.
- **Gaps, not stubs.** Missing runbooks and unwatched services are recorded as visible gaps that feed a work queue — never auto-created as empty pages that poison search.
- **Capture is zero-decision; triage is deliberate.** Saving a thought costs one sentence and no choices. Structuring it is a separate, deliberate act.
- **Existence is never the metric.** A runbook counts when it meets a content contract and has been verified against reality — at its strongest by having been exercised.

## What ships

Four services behind one nginx front, one database:

```
docs-web  :8080  ── the routed front: rendered docs site + /dashboard + /api/v1
docs-api  :8010  ── FastAPI control plane (pages, changes, model, observe, work)
dashboard :8051  ── human control surface (verb-first: Work · Know · Model · Observe)
docs-mcp  :8049  ── MCP server exposing the same contracts to agents
postgres         ── the single authority
```

## Install in five minutes

```bash
git clone <this-repo> docplane && cd docplane
cp .env.example .env
# Set POSTGRES_PASSWORD, DOCPLANE_EVENT_CURSOR_SECRET and MCP_API_KEY.
# For managed mode, also set DOCPLANE_BOOTSTRAP_TOKEN.
docker compose up --build -d postgres docs-api dashboard docs-web
```

The API container applies the ordered migrations in `db/migrations/` before it starts serving; there is no alternate bootstrap path. Check it's alive and issue your first credential:

```bash
curl -s http://localhost:8080/healthz                     # liveness + corpus counts, no auth
curl -s http://localhost:8080/.well-known/docplane.json   # machine-readable start-here

# Managed mode (the repository default): issue yourself a named contributor token
set -a; . ./.env; set +a
bash ./scripts/bootstrap-contributor.sh "Your Name" HUMAN
```

Open **http://localhost:8080/dashboard/**, connect with the token, and you're in.

**Private deployments:** if the routed hostname is already inside a VPN / SD-WAN boundary, `DOCPLANE_ACCESS_PROFILE=private_fabric` lets any reachable agent self-issue a short-lived, individually attributable token with no operator round-trip — issuance is dual-gated on the profile *and* the trusted routed front, so direct API reachability never admits it. Read [Authentication profiles](docs/architecture/authentication-profiles.md) first, and never enable it on a publicly reachable hostname.

## Learn it by using it

The tutorials take a new user from zero to a documented system:

1. [What DocPlane is](docs/tutorials/01-what-is-docplane.md) — the four verbs, the principles, the mental model.
2. [Install and first login](docs/tutorials/02-install-and-first-login.md) — environment, access profiles, tokens, a tour of the dashboard.
3. [Author your first pages](docs/tutorials/03-author-your-first-pages.md) — start documenting a real system today.
4. [Map your system](docs/tutorials/04-map-your-system.md) — build the model: cards, wires and page links.
5. [Run your work](docs/tutorials/05-run-your-work.md) — capture, triage, initiatives and honest closure.
6. [Let it observe](docs/tutorials/06-let-it-observe.md) — import monitoring rules, see coverage gaps, verify pages against reality.
7. [Connect your agents](docs/tutorials/07-connect-your-agents.md) — MCP setup, credentials, and the contract agents follow.
8. [Organise and classify](docs/tutorials/08-organise-and-classify.md) — knowledge classes, the review queue and governed reorganisation.

A fresh installation starts empty. To see the knowledge-class system end to end — the eight classes, birth classification, the suggest → curate → apply backfill and its audit surfaces — seed the generic example corpus in [`examples/knowledge-classes/`](examples/knowledge-classes/README.md).

## For agents

Everything an agent needs is discoverable over HTTP — no repository access, no guessing:

- `GET /.well-known/docplane.json` — active profile and the exact credential-acquisition path.
- `GET /api/v1/operation-contracts` — the complete operation vocabulary: JSON Schema and a full request example for every `operation_type`.
- `GET /openapi.json` — the whole surface; CI fails if contracts, schemas and examples drift apart.

The authoring contract is always **search → read exact revision → edit → validate → publish**. Review comments are optional audit events; they never authorize or block publication. A successful publication revalidates every bound revision inside the transaction, archives priors in `docs.page_versions`, applies atomically, then builds and certifies the site release. A failed build leaves the authored state durable — `POST /api/v1/publication/retry`, no re-authoring. The complete copy-pasteable cold-start path is [Agent onboarding](docs/architecture/agent-onboarding.md).

A minimal direct edit, end to end:

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

## Addressed WORK inbox (agent-to-agent)

Agents and architects that don't share a session can send each other durable, addressed messages about one initiative. This is not the capture inbox (`work_capture` / `work_inbox`), which parks unsorted ideas for triage. It is a pull-based mailbox: DocPlane stays the only writer, and every message is also an ordinary WORK activity on the initiative.

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

**Enabling it.** The inbox is optional, and an install without its secrets starts with it unavailable. Set the secret *files* in `.env`:
- `DOCPLANE_WORK_INBOX_CURSOR_SECRET_FILE`
- `DOCPLANE_WORK_INBOX_ACTIVE_KID_FILE`
- `DOCPLANE_WORK_INBOX_SIGNING_KEYS_DIR` (one file per key ID)

Secret values never go in environment variables. On a host with a governed secret store, `docker-compose.inbox-secrets.yml` mounts those directories read-only (it needs `DOCPLANE_SECRETS_GID`). Expose the inbox to a browser or hosted agent only through a restricted facade that registers just the read, contribute and ACK tools for one participant, never the aggregate MCP credential.

The full contract covers key rotation, file-permission rules and the `private_fabric` self-issue caveat: [Addressed WORK inbox](docs/architecture/work-inbox.md).

## Recovery

- Page history: `GET /api/v1/pages/{resource_id}/history`
- Read an archived revision: `GET /api/v1/pages/{resource_id}/history/{revision}`
- Restore a prior revision: `POST /api/v1/pages/{resource_id}/rollback`
- Certification state: `GET /api/v1/certification/status`
- Deployment attempts: `GET /api/v1/deployments/attempts`
- Retry the current database state: `POST /api/v1/publication/retry`

## Repository structure

- `docs-api/` — sole API authority, publication transaction and release certification
- `db/migrations/` — sole database genesis and ordered schema history
- `dashboard/` — human control surface; owns no document state
- `mcp/` — MCP tools using the same contributor API
- `mkdocs/` — rendered-site configuration
- `migration/` — corpus migration libraries: redaction/import transforms and `links.py` (link discovery, bounded link-only rewriting, route derivation)
- `scripts/` — operator tooling: contributor bootstrap, meter-list importer, invariant-register generator, link repairs, knowledge-class suggest/apply
- `docs/architecture/` — ratified design documents; `docs/tutorials/` — the learning path above

## Going deeper

- [The four-domain model](docs/architecture/DOMAIN_MODEL.md) — the ratified architecture: work/know/model/observe over one authority.
- [Guiding philosophy](docs/architecture/GUIDING_PHILOSOPHY.md) — the principles every design decision must satisfy.
- [Authentication profiles](docs/architecture/authentication-profiles.md) — managed vs private-fabric, threat model included.
- [Reorganisation control plane](docs/architecture/REORGANISATION_CONTROL_PLANE.md) — governed structural change.
- [Addressed WORK inbox](docs/architecture/work-inbox.md) — participants, grants, signed delivery, ACK and replay.
- [Redaction invariants](docs/architecture/REDACTION_INVARIANTS.md) — how secrets are kept out of the corpus, fail-closed.
- [Meter-list importer](docs/operations/METER_LIST_IMPORTER.md) — operator guide for the monitoring importer.
