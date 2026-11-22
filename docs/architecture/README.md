# DocPlane architecture

DocPlane is knowledge management and agent coordination for people who build
and run systems with AI agents. It answers two problems: the human acting as a
clipboard between agents, and agent memory being unfit for durable facts.
**Knowledge management** keeps durable facts in one governed, versioned place
that every agent and person reads. **Agent coordination** lets agents in
different tools and sessions work from shared initiatives, briefs and named
items, and message each other through the addressed WORK inbox. Messages and
reports are evidence, never authority.

These pages describe how that works. The code is the authority for behaviour;
where a page and the code disagree, the code wins and the page needs fixing.

## Foundations

- [Guiding philosophy](GUIDING_PHILOSOPHY.md): the problem, the objective and the principles every design must satisfy.
- [The four-domain model](DOMAIN_MODEL.md): work, know, model and observe over one authority, with an implementation status table.
- [Product authority](PRODUCT_AUTHORITY.md): PostgreSQL through the versioned API is the only authority; identity modes; why changes are safe.
- [Knowledge lifecycle model](KNOWLEDGE_LIFECYCLE_MODEL.md): authored knowledge, active work and generated release state, kept apart.
- [Schema genesis and upgrades](SCHEMA_GENESIS.md): one genesis migration plus immutable, additive forward migrations.

## Know

- [Page trust and maintenance](PAGE_TRUST.md): revisions, classification, revision-bound verification and verification requests.
- [Human authoring](HUMAN_AUTHORING.md): the dashboard as a client of the API, for pages and for the Work board.
- [Reorganisation control plane](REORGANISATION_CONTROL_PLANE.md): page moves and navigation as validated, atomic publications.
- [Corpus link tooling](CORPUS_LINK_TOOLING.md): deterministic link discovery and rewriting for page moves.

## Model

- [The model domain](DOMAIN_MODEL.md#the-model-domain--a-harvested-card-index): cards, typed links, generated artifacts and provenance.
- [Schema catalogue](../operations/SCHEMA_CATALOGUE.md) (operations): the generated schema catalogue and its structured projection.

## Observe

- [The observe domain](DOMAIN_MODEL.md#the-observe-domain--the-meter-list-not-the-readings): the meter list, coverage and the evidence ledger.
- [Meter-list importer](../operations/METER_LIST_IMPORTER.md) (operations): monitoring rules imported as model entities with coverage gaps.

## Work and coordination

- [The work domain](DOMAIN_MODEL.md#the-work-domain--gtd-shaped): capture, triage, initiatives, closure gates and coordination.
- [WORK briefs and named updates](WORK_BRIEF.md): one shared, revision-bound view of an initiative's actions and holds.
- [Safe WORK reads](WORK_CAPTURE_READS.md): redacted, allowlisted reads of initiatives, history and captures for agents.
- [Addressed WORK inbox](work-inbox.md): durable, signed, initiative-scoped messages between participants.
- [The Librarian](LIBRARIAN.md): designed agent-plus-validator for organising WORK; a read-only development worker exists; not finished.

## Agents and identity

- [Agent interface](AGENT_INTERFACE.md): the know and work workflows, identity modes and clients.
- [Agent onboarding](agent-onboarding.md): copy-pasteable cold start from a bare URL to a first publication and a first WORK update.
- [Authentication profiles](authentication-profiles.md): `managed` and `private_fabric`, self-issue limits and the trust boundary.

## Operations and safety

- [Redaction invariants](REDACTION_INVARIANTS.md): the canonical transform that keeps secrets out of pages and WORK reads.
- Operator guides in [`docs/operations/`](../operations/): [Invariant register](../operations/INVARIANT_REGISTER.md), [WORK catalogue](../operations/WORK_CATALOGUE.md), [Generated-condition reconciliation](../operations/GENERATED_CONDITION_RECONCILIATION.md).

## History

Kept because other pages link to them. Each says what replaced it.

- [Versioned contributor API slice](AGENT_API_IMPLEMENTATION_SLICE.md): the first contributor API slice (2026-07).
- [Domain model implementation plan](DOMAIN_MODEL_IMPLEMENTATION_PLAN.md): the original four-domain sprint plan (2026-07).
- [Card-type harvest brief](CARD_TYPE_HARVEST.md): the survey brief that shaped the model kinds (2026-07).
- [Schema observer privilege proof](SCHEMA_OBSERVER_PRIVILEGE_PROOF.md): the PostgreSQL privilege experiment for the schema observer (2026-08).
