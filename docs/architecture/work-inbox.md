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
stored envelope signature, row binding and body digest before canonical WORK
redaction. `POST .../{delivery}/ack` idempotently records receipt; it does not
resolve the task or notify the sender. Read from the beginning to replay, and
use delivery IDs for deduplication. Cursors are positions, not authorization.

Set independent high-entropy `DOCPLANE_WORK_INBOX_CURSOR_SECRET` and
`DOCPLANE_WORK_INBOX_SIGNING_SECRET` (at least 32 bytes) through the deployment
secret manager. Missing secrets fail closed. This initial version has no key
ring: changing the cursor secret invalidates cursors and changing the signing
secret makes existing deliveries unreadable. Key rotation and retention must be
specified before production use. The HMAC is verified by DocPlane, not an
independent third-party signature or proof of the correctness of a message.

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
