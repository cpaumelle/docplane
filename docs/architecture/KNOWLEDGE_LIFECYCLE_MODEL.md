# Knowledge lifecycle model

DocPlane separates three kinds of state:

- **Authored knowledge** — versioned pages published from PostgreSQL.
- **Active work** — initiatives, blockers, soaks, decisions and promotion links.
- **Generated release state** — rendered Markdown, navigation, redirects, MkDocs output and certification receipts.

Workspaces classify authored or active-work state as Reference, Operations or Work. They do not grant access. Every active named principal is a contributor.

Publication state, knowledge class, verification state and active-work state are independent. Editing a verified page makes that verification outdated; it does not block publication. Promotion from active work to durable knowledge is an explicit contributor change, not an automatic reclassification.

The domain vocabulary over these kinds of state — `work`, `know`, `model`, `observe` — is defined in [The four-domain model](DOMAIN_MODEL.md).
