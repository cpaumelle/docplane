# Agent API implementation slice

**Status:** implementing

This slice turns the endpoint-first agent contract into executable product behavior without
prematurely bypassing WP8 publication control.

## Included

- unauthenticated capability discovery with no credentials;
- named principal and scoped token authentication;
- stable page resource identifiers that survive path moves;
- bounded page reads by stable ID;
- canonical destination resolution;
- idempotent change-proposal creation;
- precise, revision-bound change operations;
- deterministic proposal validation and preview receipts;
- observable proposal and operation state.

## Deliberate hold

This slice does not merge proposals into authored state. Merge and publication require the WP8
mutation guard, candidate-state validation, immutable release orchestration and certification
wiring. Until those components land, proposals may be created and validated but not applied.

This prevents a new agent API from becoming a second, uncertified mutation path.
