# DocPlane knowledge and work lifecycle

**Status:** proposed

DocPlane is a daily operating system for human operators and software agents. It must separate
trusted knowledge, active work and generated observations instead of overloading one `lifecycle`
label with several unrelated meanings.

## Three knowledge planes

### Durable knowledge

Reviewed material that people and agents may rely on as current truth:

- reference documentation;
- operational runbooks;
- architecture and design records;
- decisions and policy;
- durable evidence and post-incident records.

Durable knowledge is published through guarded changes and WP8 certification.

### Active work

Transient coordination state used while work is being discovered, implemented, verified or held:

- initiatives and workstreams;
- engineering notes and hypotheses;
- blockers and decisions required;
- soak observations;
- handoffs;
- parking-lot items;
- promotion candidates for durable documentation.

Active work is searchable and auditable, but it must never masquerade as settled reference truth.

### Generated observations

Machine-generated snapshots such as database schemas, API specifications and service inventories.
They carry provenance and freshness but are not reviewed authored statements.

## Orthogonal state dimensions

A single status column cannot answer all lifecycle questions. DocPlane uses separate dimensions.

### Publication state

Applies to authored content:

- `DRAFT` — not generally discoverable as published knowledge;
- `PUBLISHED` — visible according to workspace permissions;
- `ARCHIVED` — retained and searchable only when archive scope is requested.

Publication state controls visibility. It does not describe what kind of knowledge the page contains.

### Knowledge class

Applies to authored content:

- `REFERENCE`
- `OPERATION`
- `ARCHITECTURE`
- `DESIGN`
- `DECISION`
- `POLICY`
- `EVIDENCE`
- `WORK_NOTE`

Knowledge class describes semantic purpose. It is not workflow status.

### Verification state

Applies to durable published knowledge:

- `UNVERIFIED`
- `VERIFIED`
- `EXPIRED`
- `OUTDATED`

A verification records owner, verifier, verification time, expiry or review time, and the exact page
revision verified. Editing a verified page invalidates that verification unless the guarded change
explicitly re-verifies the resulting revision.

### Work state

Applies only to initiatives and work items:

- `BACKLOG`
- `ACTIVE`
- `BLOCKED`
- `SOAKING`
- `PAUSED`
- `PARKED`
- `COMPLETE`
- `ABANDONED`

A work item in `SOAKING` requires a start time, expected review time, success criteria and failure or
rollback conditions. A `PARKED` item requires a reason and a review date or an explicit indefinite
owner decision. Parking is not completion and does not silently disappear.

## Workspaces

A workspace is a policy and collaboration boundary, not merely a path prefix.

Initial workspace kinds:

- `REFERENCE` — reviewed durable knowledge;
- `OPERATIONS` — runbooks and operational records;
- `WORK` — initiatives, work notes, soaks and parking lots.

Archive is a publication state, not a workspace. Deployments may create multiple workspaces of the
same kind for teams, products or programmes.

Each workspace defines:

- owners and membership;
- read, propose, review and merge permissions;
- whether direct edits are allowed;
- required reviewers and validation policy;
- default verification period;
- search and agent visibility;
- retention rules.

## Initiative model

An initiative is a first-class object rather than a specially formatted page. It contains:

- stable identifier and title;
- workspace and owner;
- work state and priority;
- objective and scope;
- created, updated and review timestamps;
- target date when applicable;
- blocker and dependency links;
- linked work notes, evidence, decisions and durable pages;
- soak criteria and observations when applicable;
- activity log;
- promotion status.

Pages may be linked to an initiative without becoming transient themselves.

## Promotion to durable knowledge

Completion does not automatically turn work notes into reference documentation.

Promotion is an explicit guarded change:

1. select the useful conclusion, procedure or decision from the work record;
2. create or update the appropriate durable page through a change proposal;
3. preserve links back to the initiative and evidence;
4. review and certify the durable change;
5. close or retain the initiative according to policy.

The durable page is extracted from proven work. The raw work log remains historical evidence and is
not reclassified merely to make the navigation tidy.

## Search and agent behaviour

Search and AI retrieval must expose state and class on every result. Default ranking should prefer:

1. verified durable knowledge;
2. current operational knowledge;
3. unverified durable knowledge;
4. active work when the query explicitly includes work scope;
5. archived material only when requested.

Agents must never cite a `WORK_NOTE`, `PARKED` item or expired page as settled truth without clearly
identifying its state. Agent-generated edits enter the same proposal and review model as human edits.

## Dashboard views

The operator console must provide at least:

- active now;
- blocked;
- soaking and due for review;
- parked and overdue for reconsideration;
- ready to promote;
- unowned durable pages;
- verification expired or due soon;
- recently completed initiatives without extracted durable documentation.

## Non-goals

- Traffic alone never changes lifecycle state.
- A file path does not determine semantic class.
- `ACTIVE` is not a valid durable-knowledge class.
- Archiving is never an automatic consequence of age or low usage.
- Generated catalog snapshots are never inserted into the authored-page lifecycle.
