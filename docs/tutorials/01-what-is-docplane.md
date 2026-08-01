# 1 · What DocPlane is

DocPlane is a **documentation control plane**: one governed database of everything you and your agents know about a system, with a browser dashboard for humans, an MCP server and HTTP API for agents, and a generated static site as the read-only public face. This page gives you the mental model; the rest of the series is hands-on.

## The problem it solves

If you run any non-trivial system — a homelab, a product, a fleet — your knowledge today is probably spread across a wiki that drifted, README files that describe last year, chat logs with agents that solved something once, and the heads of whoever did it last. Worse, if you work *with* AI agents, they read the stale docs, trust them, and confidently do the wrong thing.

DocPlane's answer is structural, not aspirational: a single authority with contracts that make drift visible and silent corruption impossible.

## The four verbs

Everything in DocPlane lives in one of four **domains of intent** — chosen so you can address knowledge out loud, to a person or an agent:

- **work** — what should happen or is happening. Ideas, captures, initiatives, blocked things, someday-maybe. *"Save that idea in work."*
- **know** — what you understand and rely on. Architecture explanations, runbooks, reference, policies, decisions, evidence. *"Check know for how that server is configured."*
- **model** — what the system structurally *is*. One card per service, node, network, database, API route; typed wires between them. *"Check model for the schema."*
- **observe** — what is watched, and the evidence of what reality shows. The meter list (not the readings), coverage gaps, verification results. *"Is anything watching this service?"*

The verbs are not four products. They are one PostgreSQL authority with four vocabularies — storage schemas, API routes, `docplane://` URIs, MCP tool prefixes and dashboard sections all share them.

## The principles you'll feel immediately

**Revision-bound writes.** Reading a page gives you its exact `revision`. Every write binds to it. If someone (or something) changed the page underneath you, your write fails loudly with a conflict instead of silently overwriting. This is what makes many agents plus humans on one corpus safe.

**Gaps, not stubs.** When something is missing — a runbook, a monitoring rule — DocPlane records a visible *gap* and feeds it into your work queue. It never auto-creates an empty page. (An earlier generation of tooling once minted hundreds of empty runbooks; this rule is the scar tissue.)

**Capture is zero-decision.** Mid-task thoughts cost one sentence to save — no title, no category, no state. Triage is a separate, deliberate act later. This is Getting Things Done applied to documentation, and the work domain is shaped around it.

**Generated content earns its place.** Catalogues generated from sources (a database schema, monitoring rules) are declared artifacts with fingerprints: they republish only when the source actually changes, are never hand-editable, and always sit behind a small authored presence page. Authored insight and generated fact never fight over the same bytes.

**Existence is never the metric.** Dashboards count *verified* pages, *exercised* runbooks, *covered* services — not files.

## The pieces

| Piece | What it is | You'll meet it in |
| --- | --- | --- |
| `docs-api` | The only authority. Pages, changes, model, observe, work — all versioned HTTP | every tutorial |
| `dashboard` | The human surface, organised by the verbs | [Tutorial 2](02-install-and-first-login.md) |
| `docs-web` | nginx front: rendered site at `/`, dashboard at `/dashboard`, API at `/api/v1` | Tutorial 2 |
| `docs-mcp` | MCP server exposing the same contracts as tools | [Tutorial 7](07-connect-your-agents.md) |
| PostgreSQL | The single source of truth | (you never touch it directly) |

Next: [Install and first login →](02-install-and-first-login.md)
