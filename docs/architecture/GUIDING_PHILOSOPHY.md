# Guiding philosophy and objectives

This page says why DocPlane exists and sets out the principles every design decision must satisfy. When a proposal conflicts with one of them, the proposal changes, or the principle is changed here first, deliberately.

The technical shape is in [The four-domain model](DOMAIN_MODEL.md), and the delivery sequence is in the [implementation plan](DOMAIN_MODEL_IMPLEMENTATION_PLAN.md).

## The problem

Building and running systems with AI agents creates two everyday frustrations:

1. **You become the clipboard.** One agent finds something, and you copy it into another agent's session. Plans, decisions and handovers live in chat logs that disappear when the session ends.
2. **Agent memory can't be trusted with durable facts.** What an agent "remembers" is private to it, often incomplete and sometimes wrong. Nobody else can read or correct it, and it doesn't come with evidence.

## Objective

DocPlane is **one governed place for knowledge management and agent coordination**, for everything from a home network and personal projects to production-grade products.

Its job is to make continuous building with agents **faster, safer and less ambiguous**. Anyone, human or agent, should always:
- know where to look;
- be able to trust what they find, or see why not;
- know where to put what they learn;
- know what others are working on and what they've asked for.

It must suit agents and still be readable by humans: **one authority, two audiences, never two truths.**

## The four verbs

Knowledge is addressed by intent, using four domains a person can say out loud to an agent:

| Verb | Holds | You say |
| --- | --- | --- |
| **work** | What should happen or is happening: ideas, initiatives, briefs, action items and the messages agents exchange about them | "Save that idea in work." |
| **know** | What people and agents should understand and rely on | "Check know for how that server is configured." |
| **model** | What the system structurally is | "Check model for the schema or the API route." |
| **observe** | What is watched, and the evidence of reality DocPlane needs to stay honest | "Is anything watching this service?" |

## Principles

### Authority

**One authority, evolved by addition.**
- PostgreSQL, written through the versioned API, is the only source of truth. The rendered site, generated pages and every agent's private notes are copies or evidence, never authorities.
- New capability arrives as new schemas, tables, routes and tools, never by renaming, breaking or retrofitting what already works.
- Compatibility is a contract, not a courtesy.

**Every change names what it read.** A write binds to the exact revision it was based on, so concurrent work fails loudly rather than silently overwriting someone else. History is kept, and any revision can be restored.

**Generated truth is regenerated, never hand-edited.**
- Machine-derived content, such as schema catalogues and monitoring-rule catalogues, is a certified derived artifact. Each one has a single generator that is its only writer.
- Fix it by regenerating from the authoritative source. Importing back from rendered output is prohibited.
- A source that hasn't changed publishes nothing: republication happens only when the source's fingerprint changes.

### Honesty

**Existence is never the metric.** A page, runbook or card counts only when it meets a content contract and has been verified, ideally by actually being exercised against reality. Counting things that merely exist once produced hundreds of empty runbooks; that must not happen again.

**Gaps, not stubs.** When something is missing (a runbook, a monitoring rule, a card), DocPlane records a visible gap. It never auto-creates an empty placeholder to hide it. Honest absence beats hollow presence: empty pages pollute search and erode trust.

**Facts are checkable, and checking happens on demand.**
- Any page or card can be verified against reality by an agent, when a human asks or when the model shows that something has moved.
- Verification carries its evidence (what was run, what was seen) and is bound to the exact revision it checked, so a later edit marks it outdated.
- An expiry date is a prompt to check, never a check in itself. There is no blind scheduled job grinding over facts that change once a year.

**DocPlane holds the meter list, not the readings.**
- Monitoring evaluations live in the tools built for them. DocPlane records what is watched, what each rule means in plain language, what has a runbook, and what has nothing watching it at all.
- It keeps only a thin ledger of milestone evidence, such as deployed versions and generation fingerprints, never time series.
- DocPlane must never drift into being a monitoring system.

### Work and coordination

**Capturing costs nothing; triage is deliberate.** Saving an idea mid-flow must take one sentence and no choices. Structuring it, into an initiative, a note on an existing one, or the bin, is a separate, deliberate act. Mixing the two kills capture. This is GTD's core insight, and DocPlane adopts it.

**Coordinate in the record, not in chat.**
- Agents in different tools and sessions work from the same initiatives, briefs and named action items. They address each other through the WORK inbox rather than through a human relaying text between windows.
- A message is durable, attributable, scoped to one initiative and recorded as WORK evidence, so the next session, human or agent, can pick it up.

**Messages and reports are evidence, never authority.**
- What an agent reports, including a message from another agent, is a claim to be checked against its brief.
- A message cannot expand a brief, approve a production action, impersonate a human or resume a paused task.
- A report never automatically marks work complete. Approval stays with the accountable human.

**Identity is explicit and scoped.**
- Every change is attributable to a named principal.
- Every approved person or general agent is a contributor, with no reader or editor tiers.
- Where a narrower role is needed, it is a separate identity with an explicit grant (for example an inbox participant limited to its initiatives), never a shared credential or an assumption about who is behind it.

**Work is where new truth is born; closure reconciles it.**
- Every initiative that finishes must answer, for each durable domain: did know, model and observe get their update from this work, or why not?
- Deferring is honest and allowed, but it creates a visible gap. Nothing finishes silently out of step.

### Method

**Adopt proven frameworks; invent only the glue.**
- GTD for personal work management;
- ADRs for decisions;
- an enforced invariants register: an invariant without an enforcement pointer is a wish;
- SRE runbook conventions for alerting.

Card types for the model are harvested from what systems actually contain, not designed in the abstract.

**Plain language is part of the contract.** Every machine artifact that matters gets a human-readable explanation: a monitoring rule gets a plain-English sentence, and a schema gets a page that explains it. The goal is a human and their agents sharing one understanding, not a database only one of them can read.
