# DocPlane usage analytics

**Status:** proposed

DocPlane must measure how humans and agents use knowledge so operators can identify high-value pages,
friction, discovery failures and credible archive candidates. Analytics create review evidence; they
never mutate content or lifecycle automatically.

## Questions the system must answer

- Which pages and sections are used most and least?
- Are readers human operators, agents or automated jobs?
- Which searches return no result or do not lead to a useful page?
- Which AI questions are unanswered, poorly rated or repeatedly asked?
- Which pages are highly used but poorly rated or repeatedly reopened?
- Which pages are never viewed but remain linked, operationally critical or required by policy?
- Which incoming URLs are broken or rescued by redirects?
- Which workspaces, initiatives and durable pages are actively contributing to current work?
- Does a page's use change after an edit, move, verification or incident?

## Event model

Usage events are append-only observations in a dedicated `analytics` schema. Every event carries:

- event identifier and timestamp;
- event type;
- stable page or initiative identifier where applicable;
- path and revision observed at event time;
- workspace identifier;
- actor class: `HUMAN`, `AGENT`, `AUTOMATION` or `ANONYMOUS`;
- pseudonymous actor and session identifiers when enabled;
- channel: `WEB`, `API`, `MCP`, `SEARCH`, `AI`, `CLI` or `WEBHOOK`;
- client identity or agent name;
- deployment and release identity;
- bounded structured metadata.

Raw page bodies, API keys, database credentials and unrestricted request payloads are never analytics
attributes.

Initial event types:

- `PAGE_VIEW`
- `PAGE_READ_API`
- `PAGE_READ_MCP`
- `SEARCH_SUBMITTED`
- `SEARCH_RESULT_CLICKED`
- `SEARCH_ZERO_RESULTS`
- `AI_QUESTION`
- `AI_ANSWER_RATED`
- `PAGE_FEEDBACK`
- `OUTBOUND_LINK_CLICKED`
- `NOT_FOUND`
- `REDIRECT_HIT`
- `CHANGE_PROPOSED`
- `CHANGE_MERGED`
- `INITIATIVE_VIEWED`
- `INITIATIVE_UPDATED`

Events that may contain user-entered text, including search and AI questions, use a separate restricted
payload store with configurable redaction and shorter retention. The main event table stores only a
payload reference and safe derived fields.

## Human and agent traffic must remain distinct

Agent reads may exceed human views by orders of magnitude. Combining them would make a page repeatedly
read by one automation appear to be the most important human documentation.

All aggregates and dashboard views therefore support independent human, agent and automation filters.
The default view shows both the combined total and the split.

## Stable identity across moves

Analytics bind to stable page identifiers, not paths alone. A move records both the stable page ID and
the path observed at the time, so history follows the page while redirect and navigation analysis can
still reconstruct the old URL.

## Collection points

- The web reader records page views, feedback and link interactions through a first-party event API.
- Docs API and MCP record successful reads server-side.
- Search records queries, result counts and selected results.
- AI retrieval records questions, cited pages and answer ratings without storing model prompts by
  default.
- The web edge records not-found and redirect-hit events.

The event ingest API must support idempotency keys, bounded batch ingestion, rate limits and explicit
client identity.

## Storage and retention

- Raw events have configurable short retention, with 90 days as a reasonable deployment default.
- Restricted query text has a shorter default retention and may be disabled entirely.
- Hourly and daily aggregates may be retained longer.
- Deletion and retention jobs are observable and fail closed rather than claiming expired data was
  removed when cleanup failed.
- Page-level analytics live in PostgreSQL or an analytics store, not as high-cardinality Prometheus
  labels. Prometheus reports only service health, ingest lag and aggregate processing failures.

## Derived signals

Analytics produce review queues, not verdicts.

### Improve candidates

- high traffic plus poor feedback;
- high traffic plus repeated search refinements;
- high traffic troubleshooting pages that may expose product or operational friction;
- frequent AI questions with low answer ratings;
- popular searches with zero results or low click-through;
- repeated redirect or not-found traffic.

### Preserve and verify candidates

- high traffic and positive feedback;
- pages frequently cited by agents;
- operational pages used during incidents;
- pages with many inbound references even when direct views are low.

### Archive-review candidates

A page enters an archive review queue only when several independent signals converge, for example:

- no human or agent use within a configured window;
- no inbound links, redirects or search selections;
- no active initiative or dependency;
- verification expired;
- no declared operational or policy criticality;
- an accountable owner approves review.

Low traffic by itself is never enough. Some of the most important recovery and policy pages should be
rarely used.

## Dashboard surfaces

The operator console must expose:

- top and bottom pages by humans, agents and combined traffic;
- trends before and after revisions;
- high-use/low-feedback pages;
- unused and unowned pages;
- search terms, zero-result searches and search-to-click success;
- AI questions, citations, unanswered questions and ratings;
- broken incoming URLs, redirects and outbound-link use;
- usage by workspace, knowledge class and lifecycle;
- CSV/JSON export through a versioned observer API.

Each page detail view should show views, unique actors where permitted, channels, last use, feedback,
search terms that led to it, citations by agents, inbound links and lifecycle context.

## Privacy and security

- Deployments choose anonymous, pseudonymous or identified human analytics.
- Raw IP addresses are not retained by default.
- Actor identifiers are keyed hashes scoped to the deployment and rotatable.
- Permission checks apply to analytics: observing that a restricted page exists can itself leak data.
- Search and AI query text is restricted to approved roles.
- Agents identify themselves; anonymous machine traffic is not silently classified as human.
- Analytics are excluded from WP8 documentation-state identity because observations do not change the
  knowledge being certified.

## Ranking use

Usage may be a weak search-ranking feature but must not create a popularity feedback loop. Verification,
access, semantic relevance and knowledge state remain stronger signals. Active-work results are not
promoted over durable verified knowledge merely because agents poll them frequently.

## Product contract

Usage analytics are a first-class DocPlane capability with stable observer APIs. Deployments may export
events to external analytics systems, but core page, search, agent and feedback insights must not depend
on a third-party SaaS service.
