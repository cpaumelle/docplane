# Credential handling

Standing rules that must hold now. Each invariant has a stable ID, the
decision that established it, and — the load-bearing part — what
enforces it. A rule nothing enforces is a wish.

## Invariants

**INV-1 — Bearers never appear in argv.**
Tokens travel in environment variables or headers, never on command
lines, where process listings and shell history capture them.
Enforced by: script contracts read `DOCPLANE_TOKEN` from the
environment; review rejects any tool that takes a token flag.

**INV-2 — Every credential is named and individually attributable.**
No shared "automation" bearer. Each principal appears in audit events
under its own identity.
Enforced by: token issuance requires a display name; audit events
record the principal on every mutation.

**INV-3 — Short-lived tokens for interactive work.**
Session work uses self-expiring tokens; long-lived credentials exist
only for standing automation, and each has an owner and a rotation
date.
Enforced by: self-issued tokens carry a TTL; the credential inventory
lists owner and next rotation for every long-lived token.

**INV-4 — Revocation is tested, not assumed.**
A revoked credential must be rejected with an explicit error, and
someone must have seen that rejection happen this quarter.
Enforced by: the quarterly access review includes one live revocation
check against a scratch token.

## Amending this page

Invariants are amended by decision, not by edit: raise an ADR, link it,
then update the register to match.
