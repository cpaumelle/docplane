# Guiding philosophy and objectives

This document states why DocPlane exists and the principles every design
decision must satisfy. The technical shape is in
[The four-domain model](DOMAIN_MODEL.md); the delivery sequence is in the
[implementation plan](DOMAIN_MODEL_IMPLEMENTATION_PLAN.md).

## Objective

DocPlane is the single place of knowledge about the fabric — home network,
personal projects and production-grade products alike. Its job is to make
continuous building with agents **faster, safer and less ambiguous**: an
agent or a human should always know where to look, trust what they find, and
know where to put what they learn. It must be agent-friendly and
human-readable at the same time — one authority, two audiences, never two
truths.

## The four verbs

Knowledge is addressed by intent, using four domains a person can say out
loud to an agent:

- **work** — what should happen or is happening. "Save that idea in `.work`."
- **know** — what people and agents should understand and rely on. "Check
  `.know` for how that server is configured."
- **model** — what the fabric structurally is. "Check `.model` for the
  schema or the API route."
- **observe** — what is watched, and the evidence of reality DocPlane needs
  for its own honesty. "Is anything watching this service?"

## Principles

**One authority, additive evolution.** PostgreSQL through the versioned API
is the only source of truth. New capability arrives as new schemas, tables,
routes and tools — never by renaming, breaking or retrofitting what already
works. Compatibility is a contract, not a courtesy.

**Existence is never the metric.** A page, runbook or card counts only when
it meets a content contract and has been verified — ideally by having been
*exercised* against reality. Counting things that merely exist produced
hundreds of empty runbooks once; it must not happen again.

**Gaps, not stubs.** When something is missing — a runbook, a monitoring
rule, a card — DocPlane records a visible gap and never auto-creates an
empty artifact to paper over it. Honest absence beats hollow presence:
empty pages poison search and burn trust.

**Capture is zero-decision; triage is deliberate.** Saving an idea mid-flow
must cost one sentence and no choices. Structuring that idea — into an
initiative, a note, or the bin — is a separate, deliberate act. Mixing the
two kills capture; this is GTD's core insight and DocPlane adopts it.

**Work is where new truth is born; closure reconciles it.** Every initiative
that finishes must answer, per durable domain: did `know`, `model` and
`observe` get their update from this work — or explicitly why not? Deferral
is honest and allowed, but it mints a visible gap. Nothing finishes silently
out of sync.

**DocPlane holds the meter list, not the readings.** Monitoring evaluations
live in the tools built for them. DocPlane records what is watched, what
the rules mean in plain language, what has a runbook, and what has nothing
watching it at all. It keeps only a thin ledger of milestone evidence
(deployed versions, generation fingerprints) — never time series. DocPlane
must never drift into being a monitoring system.

**Generated truth is regenerated, never hand-edited.** Machine-derived
content — schema catalogues, monitoring-rule catalogues — is a certified
derived artifact. Remediation is regeneration from the authoritative
source; reverse-import from rendered output is prohibited. A stable source
publishes nothing: republication happens only when the source fingerprint
changes.

**Facts are checkable, and checking is on demand.** Any page or card can be
verified against the fabric by an agent, on a human's trigger or when the
model graph signals that reality moved. Verification carries evidence —
what was run, what was seen — bound to the exact revision checked. Expiry
dates are prompts to check, never checks themselves; there is no blind cron
grinding over facts that change once a year.

**Adopt proven frameworks; invent only the glue.** GTD for personal work
management, ADRs for decisions, an enforced invariants register (an
invariant without an enforcement pointer is a wish), SRE runbook
conventions for alerting. Card types for the model are harvested from what
the fabric actually contains, not designed in the abstract.

**Plain language is part of the contract.** Every machine artifact that
matters gets a human-readable explanation — a monitoring rule gets a
plain-English sentence, a schema gets a presence page — because the product
goal is a human and their agents staying in shared understanding, not a
database only one of them can read.
