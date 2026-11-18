# Knowledge lifecycle model

DocPlane separates three kinds of state:

- **Authored knowledge**: versioned pages published from PostgreSQL.
- **Active work**: captures, initiatives and their activities, briefs with named
  items, closure dispositions, links, and addressed inbox messages.
- **Generated release state**: rendered Markdown, navigation, redirects, MkDocs
  output and certification receipts.

Workspaces classify authored or active-work state as Reference, Operations or
Work. They do not grant access. Access depends on the principal's authorization
mode: contributors share the same rights, and the scoped modes are described in
[Agent interface](AGENT_INTERFACE.md#identity).

Publication state, knowledge class, verification state and active-work state are
independent. Editing a verified page makes that verification outdated; it does
not block publication.

Knowledge moves from work to durable domains only through explicit acts:

- promotion from active work to durable knowledge is an explicit contributor
  change, not an automatic reclassification;
- finishing an initiative requires a disposition for know, model and observe
  (updated, not required, or deferred as a visible gap);
- a brief item marked done records a claim with evidence; it never publishes or
  verifies anything.

The domain vocabulary over these kinds of state (`work`, `know`, `model`,
`observe`) is defined in [The four-domain model](DOMAIN_MODEL.md).
