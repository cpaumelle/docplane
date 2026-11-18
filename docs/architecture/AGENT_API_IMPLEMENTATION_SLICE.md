# Versioned contributor API

> **Historical record** of the first contributor API slice as of 2026-07-27.
> Current behaviour: [Agent interface](AGENT_INTERFACE.md),
> [Product authority](PRODUCT_AUTHORITY.md) and
> [Agent onboarding](agent-onboarding.md). Since this slice, DocPlane has added
> the model, observe and WORK coordination APIs, and two scoped identity modes
> (`SCOPED_AUTOMATION` and `WORK_COLLABORATOR`) alongside contributors.

**Status:** implemented

The assembled API provides capability discovery, named contributor authentication, stable page identities, bounded reads, canonical search, revision-bound changes, deterministic validation, direct publication, version history, rollback, event cursors, active work, page trust and structural reorganisation.

There is one publication path for dashboard, HTTP and MCP clients. It does not use shared API keys, workspace roles, proposal submission, approval or separate merger authority.
