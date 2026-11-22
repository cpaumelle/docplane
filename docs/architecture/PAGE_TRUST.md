# Page trust and maintenance

DocPlane keeps content revision, metadata classification and verification
separate.

- A page has a stable resource identity and a mutable content revision.
- Classification records workspace, publication state, knowledge class,
  criticality, provenance (`AUTHORED` or `GENERATED`) and accountable owner.
- Verification applies to one exact content revision and becomes outdated after
  any later edit.
- Expiry and maintenance queues create visible work. They never rewrite,
  reclassify or archive content automatically.
- Workspaces classify state and do not grant permissions. Contributors have the
  same document access; scoped principals are limited as described in
  [Agent interface](AGENT_INTERFACE.md#identity).
- Usage statistics are review evidence owned by the dashboard and are not part of
  page authority.

## Verification on demand

A verification request (`/api/v1/verification-requests`) asks an agent to check
a page, a section or a model entity's pages against reality. It carries a
briefing with each page's exact revision. A change to a model entity can mint a
request for the pages linked to it (graph ripple). Expiry is a prompt only;
there is no scheduled re-verification.

The briefing states a correction policy per page
(`docs-api/app/verification_api.py`): ordinary pages may be corrected by direct
publication, while `OPERATIONAL_CRITICAL` and `POLICY_REQUIRED` pages should get
a pending change for a human. This is guidance to the executing agent, not a
publication gate.

## Safety boundary

Publication does not require review. Exact revision checks, immutable
page-version snapshots, audit events, release certification and rollback
provide the safety boundary. A `GENERATED` page can be changed only by its
declaring generator; fix it by regenerating.
