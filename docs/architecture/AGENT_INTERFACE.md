# DocPlane agent interface

**Status:** proposed

DocPlane is designed for continuous use by many agents as well as human operators. An unfamiliar
authorized agent must be able to discover the platform, find the canonical existing knowledge, retrieve
only the context it needs, propose a precise update and observe publication without SSH, filesystem
access, direct SQL or deployment-specific helper scripts.

## Invariant

**A-DOCPLANE-ENDPOINT-FIRST-1:** Normal discovery, inspection, mutation, review, publication and
observability are exposed through versioned programmatic endpoints. SSH, container shells and direct
PostgreSQL inspection are break-glass diagnostic tools, never the normal agent workflow.

Repeated shell access for a routine operation is evidence of a missing product endpoint.

## Lessons retained from the CharlieHub agent guide

The existing guide established useful controls that remain product requirements:

- PostgreSQL, not rendered Markdown, is authoritative;
- optimistic concurrency is service-enforced;
- move, reparent, archive, restore and redirects are explicit operations;
- validation and dry-run rendering are available before publication;
- deployment is automatic and observable;
- navigation and resource identity are separate;
- agents search for and extend canonical content rather than create duplicates;
- failures return actionable rules and override requirements.

The community product replaces deployment-specific bootstrap, raw curl recipes, shared API keys,
whole-page last-writer-wins updates and path-only identity with stable discovery, named principals,
structured retrieval and precise change operations.

## Discovery

Every deployment exposes:

`GET /.well-known/docplane.json`

The capability document contains no credential. It declares:

- product and API contract versions;
- OpenAPI and MCP endpoints;
- authentication issuer, methods and scopes;
- human application and dashboard URLs;
- supported knowledge planes and workspace capabilities;
- batch, payload and rate limits;
- event-stream and webhook capabilities;
- deprecation policy;
- links to compact agent instructions and generated clients.

OpenAPI is the normative HTTP contract. MCP is the preferred interactive agent interface. A compact
`skill.md` explains workflow and policy rather than duplicating endpoint schemas in a long guide.

## Named agent identities

Agents are principals, not anonymous holders of a deployment-wide key. Each identity records:

- stable principal ID and owner;
- client and optional model metadata;
- workspace roles and operation scopes;
- credential issue, expiry and rotation state;
- rate and concurrency policy;
- audit and analytics classification.

Initial scopes include `docs:read`, `docs:propose`, `docs:write-direct`, `docs:review`, `docs:merge`,
`work:read`, `work:update`, `catalog:read`, `analytics:read` and `events:subscribe`.

A trusted network may be an additional deployment boundary, but it is not the product identity model.

## Stable resources

Agents address resources by immutable IDs rather than mutable paths:

- `docplane://workspaces/{workspace_id}`
- `docplane://pages/{page_id}`
- `docplane://pages/{page_id}/revisions/{revision}`
- `docplane://initiatives/{initiative_id}`
- `docplane://changes/{change_id}`
- `docplane://catalog/sources/{source}/snapshots/{fingerprint}`

A page move changes path metadata and redirects, not the page resource identity.

## Core agent workflow

### 1. Search

`docplane.search` supports natural-language and exact queries plus filters for knowledge plane,
workspace, publication state, knowledge class, verification state, work state, owner and history.

Each result includes stable ID, path, title, snippet, authority state, owner, revision, verification,
related resources and the caller's permitted operations.

Agents do not start by listing or downloading the whole corpus.

### 2. Resolve the canonical destination

`docplane.resolve_canonical` answers where information should live. It returns:

- the preferred existing page or initiative;
- alternatives and why they differ;
- duplicate or superseded candidates;
- whether a new resource is justified;
- required workspace and metadata if creation is permitted.

Creating a durable page requires an unambiguous resolution result or an explicit creation reason and
acknowledgement of similar content. Anti-fragmentation becomes a service invariant rather than a checklist
an agent may omit.

### 3. Read bounded context

`docplane.read` supports:

- `summary` — metadata and abstract;
- `outline` — heading IDs and section hashes;
- `section` — one requested section;
- `full` — complete current content;
- `rendered` — published representation;
- `edit_context` — content plus backlinks, validation, ownership and permissions.

This keeps routine agent context small and intentional.

### 4. Create a change

A change is a first-class object based on exact revisions or a complete documentation-state identity. It
may contain several atomic operations across several pages. It records author, purpose, operations,
validation, preview, reviewers, comments, merge result and publication receipt.

Low-risk workspaces may allow validate-and-merge in one call. Durable reference and operations spaces
normally require review.

### 5. Apply precise operations

Agents update the smallest semantic unit that expresses the change:

- patch structured metadata;
- replace a named heading section;
- insert before or after a stable heading ID;
- append to a designated observation or activity stream;
- replace an exact content range identified by hash;
- add or remove a link;
- create from a registered template;
- move, reparent, archive, restore or redirect;
- replace the complete document only as an explicit fallback.

Markdown operations target stable heading IDs or content hashes, not line numbers. Every operation carries
an expected page revision and, where relevant, an expected section hash.

A stale operation returns the current revision, changed sections and rebase options. It never silently
overwrites newer work. Wildcard last-writer-wins updates are reserved for explicitly authorized import or
repair operations and are not the normal agent quickstart.

### 6. Validate and preview

Validation reports stable rule codes for:

- workspace and metadata policy;
- canonicality and duplicate risk;
- links, anchors and redirects;
- navigation conflicts;
- lifecycle, ownership and verification requirements;
- style and content schemas;
- WP8 publication readiness.

Multi-page changes validate against one candidate state and receive one preview release so references and
navigation cannot be half-updated.

### 7. Submit, merge and observe

A successful operation returns a receipt containing:

- change and operation IDs;
- affected resources and new revisions;
- proposal, review or merge state;
- validation summary and preview URL;
- deployment cycle and release IDs when merged;
- certification and publication status;
- event cursor for follow-up.

Long-running work returns a durable task or change ID. Agents poll or subscribe to state rather than
retrying mutations or watching shell output.

## MCP surface

MCP exposes resources, tools and workflow prompts.

Initial tools:

- `docplane.search`
- `docplane.resolve_canonical`
- `docplane.read`
- `docplane.get_edit_context`
- `docplane.create_change`
- `docplane.apply_change_operations`
- `docplane.validate_change`
- `docplane.submit_change`
- `docplane.merge_change`
- `docplane.get_publication_status`
- `docplane.list_work`
- `docplane.update_work`
- `docplane.search_catalog`
- `docplane.subscribe`

Workflow prompts cover updating canonical documentation after a system change, promoting completed work
to durable knowledge, reviewing a change and reconciling generated drift. Prompts are convenience, not
hidden authority.

## HTTP, SDK and CLI

All product functions are also available through versioned HTTP endpoints. DocPlane publishes generated
clients plus a small opinionated SDK implementing discovery, authentication, pagination, idempotency,
precise changes, subscriptions and typed errors.

The CLI mirrors product objects rather than raw HTTP:

```text
docplane search "postgres migration ledger"
docplane read <page-id> --outline
docplane change create --title "Correct migration procedure"
docplane change replace-section <change-id> <page-id> --heading rollback --file rollback.md
docplane change validate <change-id>
docplane change submit <change-id>
```

Helper scripts may wrap the CLI but never contain unique authority or deployment-only logic.

## Idempotency and atomicity

Every mutation accepts an idempotency key scoped to principal and operation. Retries return the original
receipt rather than duplicate resources or changes.

A multi-resource change:

1. resolves and locks exact revisions;
2. applies every operation to a candidate state;
3. validates the candidate as a whole;
4. creates one semantic diff and preview;
5. commits all authored-state changes atomically or none;
6. creates one durable WP8 deployment demand.

## Incremental synchronization

Agents that maintain knowledge continuously do not repeatedly rescan the corpus. DocPlane exposes:

- MCP resource subscriptions and list-change notifications;
- a permission-filtered append-only event stream with opaque cursors;
- signed webhooks with replay IDs and retry policy;
- HTTP delta reads such as `GET /api/events?after={cursor}`;
- changed-resource summaries that omit bodies until requested.

Events cover page revisions, moves, lifecycle and verification changes, initiative updates, reviews,
publication, schema snapshots and policy changes.

## Error contract

Errors use stable codes and structured remediation:

```json
{
  "error": {
    "code": "BASE_REVISION_STALE",
    "message": "The page changed after this operation was prepared.",
    "resource": "docplane://pages/01J...",
    "currentRevision": "...",
    "retryable": true,
    "suggestedAction": "Read the changed sections and rebase the operation.",
    "details": {}
  }
}
```

Agents never parse prose to distinguish conflicts, validation refusals, permission failures and transient
service errors.

## Observability

Agent reads, searches, canonical resolutions, citations, proposals, validation failures and merges emit
usage events under the analytics contract. Human, agent and automation traffic remain separately
queryable.

Operational endpoints expose health, readiness, queue state, migrations, deployment attempts,
certification, event lag, webhook delivery, backup jobs and catalog freshness. Agents do not need SSH to
determine normal operational state.

## Minimum agent-ready bar

DocPlane is not agent-ready until an unfamiliar authorized agent can, without repository or host access:

1. discover capabilities and authentication;
2. search and resolve the canonical resource;
3. read bounded edit context;
4. propose a precise concurrency-safe update;
5. receive validation and preview;
6. submit or merge according to policy;
7. observe certification and publication;
8. subscribe to subsequent relevant changes.
