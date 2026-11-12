# Addressed WORK inbox (proposed)

DocPlane is the sole writer. Each collaboration participant is a distinct named
`WORK_COLLABORATOR` principal with a separate credential. It is denied general
contributor endpoints. Bootstrap administration creates participants and grants
initiative-specific reader or contributor access; expiration, principal/token
revocation and grant revocation are checked on use. The participant and role are
bound by the server, never by message fields. A shared credential identifies its
principal only; it cannot identify a particular human, model or session. Do not
call a browser architect Charles or give a browser the aggregate operational MCP
credential. A restricted facade must register only the inbox tools and bind each
participant to a separately authenticated connection.

`POST /api/v1/work-inbox/{initiative}/messages` accepts recipient_id, body and
optional reported_producer plus a canonical UUID Idempotency-Key. The reported
producer is a claim. The server refuses unsafe input, checks both grants, and
atomically writes a WORK activity, audit event, signed envelope, delivery and
receipt. Identical retries replay; changed intent with the same key is refused.
A per-recipient, per-initiative counter is updated under a transaction lock,
held until commit, so a later commit cannot pass an earlier uncommitted position.
A failed transaction leaves none of these writes behind.

`GET` on the same path reads a bounded page after an opaque principal- and
initiative-bound cursor. Every read rechecks current grants and verifies the
stored envelope HMAC, row binding and body digest before canonical WORK
redaction. A failed integrity check returns a flagged item with its body and
unverified envelope withheld; its scoped database position allows pagination
to continue. Investigate such a failure before acting on the affected message. `POST .../{delivery}/ack` idempotently records receipt; it does not
resolve the task or notify the sender. Read from the beginning to replay, and
use delivery IDs for deduplication. Cursors are positions, not authorization.

Configure `DOCPLANE_WORK_INBOX_CURSOR_SECRET_FILE` and
`DOCPLANE_WORK_INBOX_ACTIVE_KID_FILE` as protected files.
`DOCPLANE_WORK_INBOX_SIGNING_KEYS_DIR` contains one protected file per key ID;
hidden names (such as a materializer's `.chsec-*.tmp` staging file) are
ignored. Files must be regular and non-symlink, and either owner-only (0400 or
0600) or exactly 0440 owned by `root` and GID 10012 (`docplane-secrets`), the
governed group-read contract that lets the API read them through a
supplemental group. Any other group or world access is refused. Secret values never
belong in environment variables in production. An environment value and its
file configuration together are rejected. Add a key file before switching the
active key ID; retain old key files while their messages must remain readable.
Removing a key flags its messages as unverifiable. Rotating the cursor secret
invalidates old cursors; clients restart and deduplicate by delivery ID.

On a host using SECRETS-V3, enable `docker-compose.inbox-secrets.yml`
(`COMPOSE_FILE=docker-compose.yml:docker-compose.inbox-secrets.yml`). It
bind-mounts the governed `docplane-inbox-api` and `docplane-inbox-signing`
directories read-only and adds supplemental GID 10012 to the API, so a new key
file or an atomic replacement is visible without recreating the container.
Switching the overlay on or off changes the container definition and needs
`docker compose up -d docs-api`.

The HMAC allows DocPlane to check that stored content is intact; clients cannot
verify it and it does not prove the message is correct or authorized. Provenance
comes from the authenticated sender principal and its server-side grant. A
participant token defaults to 90 days unless an expiry is supplied. ACK writes
one audit event on first receipt, without publishing an inbox notification.

Under the `private_fabric` profile, fabric access to `/api/v1/auth/self-issue`
can create a general contributor credential. A browser architect's network and
facade path must not reach that endpoint; the scoped token alone does not prevent
self-issuance through a separate reachable path. Qualify this isolation before
any browser production use.

Messages are data, never authority: they cannot expand a brief, approve a
production action, impersonate a human, or resume a paused task. The current
contract is pull-only for Codex, Claude Code, ChatGPT Work and Claude.ai.
Claude.ai direct MCP reads occur during an active turn. A separate one-shot
scheduled message may resume the same conversation after an active turn ends;
recurring scheduled tasks start fresh sessions. Both require deployed-client
qualification. Claude Code Channels, ChatGPT MCP Events and Codex continuation
are later adapters; generic SSE is not a wake guarantee.

This is development code only. No credential issuance, facade deployment,
production migration, restart or adapter qualification follows from this PR.
