# DocPlane product capability review

**Status:** proposed

This review compares the intended DocPlane product with current documentation and team-knowledge
platform patterns. The target is not a static documentation generator. It is a dynamic, daily operating
system used concurrently by a human operator, many software agents and automated collectors.

## Product conclusion

DocPlane needs five first-class surfaces:

1. **Durable knowledge** — reviewed reference, operations, decisions, policy and evidence.
2. **Active work** — initiatives, WIP, blockers, soaks, handoffs and parking lots.
3. **Generated observations** — schemas and other machine-observed technical catalogs.
4. **Collaboration and change control** — proposals, review, comments, conflicts and merge policy.
5. **Usage and quality intelligence** — traffic, search, feedback, AI questions, freshness and drift.

WP8 certifies published knowledge and releases. It does not replace the active-work, collaboration or
usage models.

## Patterns confirmed in established products

### Safe collaborative editing

Modern documentation platforms use branch-like change requests, previews, diffs, reviewer assignment,
merge rules and conflict resolution. Human and agent-authored changes should enter the same proposal
model rather than agents obtaining a separate privileged mutation path.

### Spaces and policy boundaries

Products organize knowledge into spaces or collections with separate ownership, permissions and
publishing rules. DocPlane workspaces should therefore be first-class policy boundaries, not inferred
from path prefixes.

### Ownership and expiring verification

Knowledge systems increasingly attach owners and time-bounded verification to important pages. Expired,
unowned and outdated content becomes an actionable maintenance queue, and verified content receives a
stronger trust signal in search and AI retrieval.

### Knowledge-management control panel

A useful operator surface supports bulk filtering and action across outdated, empty, unowned, inactive,
expired and archived documents. Page-by-page maintenance is not sufficient for a large, agent-updated
corpus.

### Usage, search and feedback analytics

Mature systems track page events and unique visitors, search terms, zero-result searches, feedback,
broken incoming URLs, outbound links, AI questions and answer ratings. Traffic must be interpreted with
other signals: high views can mean value or friction, while low views can describe either obsolete
content or a critical emergency procedure.

### Agent-aware maintenance

Newer systems expose MCP/API access, unanswered-question insights and agent-proposed drift corrections
that remain subject to human approval. DocPlane should treat agent identity, citations, proposals and
review outcomes as first-class audit data.

### Extensible search and generated sources

Developer portals use pluggable collectors and search indices to combine authored docs with software
catalogs and other technical sources. The `tbls` catalog should therefore be the first implementation
of a general generated-source contract rather than a one-off database feature.

## Current DocPlane coverage

### Present or in active implementation

- PostgreSQL-authored pages and version history;
- navigation and page moves;
- MCP and HTTP agent interfaces;
- MkDocs rendering;
- WP8 certified state and durable deployment attempts;
- PostgreSQL redirect authority;
- generated database schema catalog foundation;
- structural dashboard foundation.

### Missing P0 capabilities

These are required before DocPlane is credible as a daily multi-agent knowledge manager.

#### Human authoring application

The operator needs a browser authoring surface with Markdown editing, preview, metadata, history and
state-aware navigation. An observer dashboard alone is not a usable human management application.

#### Change proposals and review

Required objects and flows:

- draft proposal based on an exact source revision or state identity;
- multi-page proposal support;
- human or agent author;
- diff and preview;
- comments and requested reviewers;
- validation results;
- merge policy per workspace;
- conflict detection and resolution;
- merge, reject and abandon outcomes;
- immutable audit history.

Direct edits may remain configurable for low-risk workspaces, but durable reference and operational
knowledge should default to proposals.

#### Active workspaces and initiatives

WIP, soaking and parking cannot remain conventions embedded in page prose. They require first-class
workspaces, initiatives, work states, owners, review dates, blockers, dependencies, soak criteria and
promotion links to durable documentation.

#### Ownership and verification

Pages need owners, verification records, expiry, review queues and explicit outdated flags. Search and
agents must understand those signals.

#### Usage analytics

Human and agent reads, searches, feedback, AI questions, links, redirects and not-found events require a
first-party event model and observer APIs.

#### Identity, roles and permissions

Community deployments need stable identities for humans, agents and automations; workspace roles;
page-level exceptions where necessary; service credentials; and permission-aware search and analytics.

#### Activity feed and notifications

Humans and agents need a common inbox for review requests, mentions, verification expiry, blocked work,
soak review, failed deployments, catalog staleness and watched-page changes.

#### Backup, restore, import and export

A first-class product requires documented, tested and observable backup/restore plus deterministic
content export/import. These cannot remain deployment folklore.

## Missing P1 capabilities

- in-context comments and discussion threads;
- subscriptions and watch rules;
- backlinks, dependency graph and orphan detection;
- reusable templates and content schemas;
- bulk ownership, verification, archive and move operations;
- webhooks and an append-only change/event feed;
- search analytics and AI answer-gap workflows;
- agent drift proposals from connected systems;
- attachment and asset lifecycle;
- API tokens with scoped permissions and rotation;
- retention controls and audit export;
- observer APIs for dashboard and external automation.

## Missing P2 and extension capabilities

- language localization;
- product and version variants;
- public, private and audience-controlled publishing;
- OpenAPI, AsyncAPI, GraphQL and protobuf generated documentation;
- generic source collectors beyond `tbls`;
- external search backends;
- SSO and directory synchronization;
- plugin/add-on framework;
- offline or static export packages;
- reusable snippets and transclusion with dependency tracking.

These should be designed as extension points now but need not block the CharlieHub replacement.

## Information model correction

The current legacy model conflates:

- database publication state such as `active` or `archived`;
- semantic labels such as `REFERENCE` and `OPERATION`;
- workflow labels such as `ACTIVE`, `PAUSED` and `BACKLOG`.

This must be corrected before new lifecycle schema is committed. Publication, knowledge class,
verification and work state are independent dimensions. See `KNOWLEDGE_LIFECYCLE_MODEL.md`.

## Search model

DocPlane search should index all permitted planes while making their authority explicit. Every result
must expose:

- workspace;
- publication state;
- knowledge class or work state;
- owner;
- verification state and expiry;
- last update;
- generated-source freshness where applicable;
- stable citation identity.

Default search and AI answers prefer verified durable knowledge. Active work and archived evidence are
available through explicit filters and may be cited only with their state disclosed.

## Agent model

Agents are named principals, not anonymous API keys. Each agent has:

- stable identity and owner;
- scoped roles and workspace access;
- client and model metadata where appropriate;
- proposal, read and citation audit history;
- rate and concurrency limits;
- idempotency requirements;
- an optional human-approval requirement by mutation class.

Agents use the same proposal, review, verification and lifecycle rules as humans. Automation does not
create a second source of truth.

## Recommended delivery slices

1. Merge the product-authority foundation.
2. Complete WP8 core and certified mutation/release wiring.
3. Add workspace, initiative and lifecycle schema before expanding the dashboard lifecycle UI.
4. Add first-party usage-event ingest, aggregation and observer APIs.
5. Add change proposals, reviews and the human authoring application.
6. Add ownership, verification, maintenance queues and notification inbox.
7. Connect dashboard views to versioned observer APIs for knowledge, work, certification, analytics and
   catalog state.
8. Add identity/RBAC and permission-aware search before multi-user community deployment.
9. Add generic generated-source and webhook extension contracts.

## Promotion bar

A capability is not complete merely because an API table exists. Each major surface requires:

- a stable schema and versioned API contract;
- human UI where humans are expected to operate it;
- MCP/API tools where agents are expected to operate it;
- permissions and audit;
- tests and failure semantics;
- dashboard health and maintenance views;
- backup/export behaviour;
- documentation for self-hosted operators.
