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
- `scripts/` — operator tooling: contributor bootstrap, meter-list importer, link repairs, knowledge-class suggest/apply
- `docs/architecture/` — ratified design documents; `docs/tutorials/` — the learning path above

## Going deeper

- [The four-domain model](docs/architecture/DOMAIN_MODEL.md) — the ratified architecture: work/know/model/observe over one authority.
- [Guiding philosophy](docs/architecture/GUIDING_PHILOSOPHY.md) — the principles every design decision must satisfy.
- [Authentication profiles](docs/architecture/authentication-profiles.md) — managed vs private-fabric, threat model included.
- [Reorganisation control plane](docs/architecture/REORGANISATION_CONTROL_PLANE.md) — governed structural change.
- [Redaction invariants](docs/architecture/REDACTION_INVARIANTS.md) — how secrets are kept out of the corpus, fail-closed.
- [Meter-list importer](docs/operations/METER_LIST_IMPORTER.md) — operator guide for the monitoring importer.
