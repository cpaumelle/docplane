# Addressed WORK inbox

The addressed WORK inbox lets agents and architects that do not share a session
send each other durable messages about one initiative. It is how DocPlane
removes the human from the middle of agent-to-agent handovers: instead of a
person copying text between windows, one participant writes to another through
DocPlane, and the message is kept as WORK evidence on the initiative.

It is not the capture inbox. The capture inbox (`/api/v1/work/captures`) parks
unsorted ideas for triage. The addressed inbox is a pull-based mailbox between
named participants.

The inbox is optional. An installation without its secret files starts with it
unavailable. Code: `docs-api/app/work_inbox_api.py`,
`docs-api/app/work_inbox_cursor.py`, `docs-api/app/inbox_secrets.py` and
migration `db/migrations/020_work_inbox.sql`.

## Participants and grants

DocPlane is the only writer. Each participant is a distinct principal with
authorization mode `WORK_COLLABORATOR` and its own credential.

- A `WORK_COLLABORATOR` is refused on every general contributor endpoint
  (`PRINCIPAL_WRITE_SCOPE_DENIED`). An empty or expired set of grants never turns
  it into a contributor.
- An operator holding the bootstrap token creates participants and grants each
  one access to specific initiatives:
  - `POST /api/v1/bootstrap/work-participant-identities` creates the principal
    only. The receipt holds no credential, so a lost response can be replayed
    without leaving an orphan token. Its token is then issued through the
    idempotent `POST /api/v1/bootstrap/principals/{id}/tokens` route.
  - `POST /api/v1/bootstrap/work-participants` creates a principal and returns a
    token in one call.
  - `PUT /api/v1/bootstrap/work-grants/{initiative}/{principal}` grants one
    initiative with `role` `AGENT` or `ARCHITECT`, `can_contribute` and an
    optional `expires_at`. `DELETE` on the same path revokes it.
- A participant token defaults to 90 days unless an expiry is supplied.
- Token, principal and grant expiry and revocation are checked on every request.
- The server binds the sender and role from the authenticated principal and its
  grant, never from message fields.

A shared credential identifies its principal only. It cannot tell you which
human, model or session is using it. Do not label a browser-based architect as a
named person, and never give a browser the aggregate MCP credential. A facade
for such a client must register only the inbox tools and bind each participant
to a separately authenticated connection.

## Sending

`POST /api/v1/work-inbox/{initiative}/messages` accepts `recipient_id`, `body`
and an optional `reported_producer`, plus a canonical UUID `Idempotency-Key`.

- `reported_producer` is recorded as a claim, not verified.
- The server refuses unsafe input and checks both the sender's and the
  recipient's grants.
- One transaction writes the WORK activity, an audit event, a signed envelope,
  the delivery and a receipt. If the transaction fails, none of them is left
  behind.
- An identical retry replays the original receipt. Reusing a key with a
  different request is refused.
- Each recipient has a position counter per initiative. It is updated under a
  row lock held until commit, so a later commit can never take a position ahead
  of an earlier, still-uncommitted one.

## Reading and acknowledging

`GET` on the same path returns a bounded page after an opaque cursor. The cursor
is bound to the principal and the initiative. It is a position, not an
authorisation.

Every read rechecks current grants and verifies the stored envelope's HMAC, its
row binding and the body digest, then applies canonical WORK redaction. If an
integrity check fails, the item comes back flagged, with its body and the
unverified envelope withheld; its position still lets pagination continue.
Investigate such a failure before acting on the message.

`POST .../messages/{delivery}/ack` records receipt, idempotently. It writes one
audit event on first receipt. It does not resolve the task, notify the sender or
publish a notification.

To replay, read from the beginning again and deduplicate by delivery ID.

## What the signature means

The HMAC lets DocPlane check that stored content has not changed. Clients cannot
verify it, and it does not prove the message is correct or authorised.
Provenance comes from the authenticated sender principal and its server-side
grant.

## Secret files

The inbox reads its secrets from protected files, never from environment
variables in production:

- `DOCPLANE_WORK_INBOX_CURSOR_SECRET_FILE` and
  `DOCPLANE_WORK_INBOX_ACTIVE_KID_FILE` name single files.
- `DOCPLANE_WORK_INBOX_SIGNING_KEYS_DIR` holds one file per key ID. Hidden names
  (such as a materialiser's `.chsec-*.tmp` staging file) are ignored.

Each file must be a regular file, not a symlink, and either owner-only (0400 or
0600) or exactly 0440, owned by `root` and by a group the API process holds (the
deployment grants it as a supplementary group). Any other group or world access,
or a group the process does not hold, is refused. Setting both an environment
value and its file is rejected.

Key rotation:

- Add a new key file before switching the active key ID.
- Keep old key files while their messages must stay readable. Removing a key
  flags its messages as unverifiable.
- Rotating the cursor secret invalidates old cursors. Clients restart from the
  beginning and deduplicate by delivery ID.

On a host with a governed secret store, enable `docker-compose.inbox-secrets.yml`
(`COMPOSE_FILE=docker-compose.yml:docker-compose.inbox-secrets.yml`). It mounts
the `docplane-inbox-api` and `docplane-inbox-signing` directories read-only and
adds the store's group to the API container as a supplementary group. Set
`DOCPLANE_SECRETS_GID` to that group's numeric host GID; the overlay refuses to
start without it. Directories are mounted rather than files, so a new or
atomically replaced key file is visible without recreating the container.
Turning the overlay on or off changes the container definition and needs
`docker compose up -d docs-api`.

## Security caveats

- **Self-issue bypass.** Under the `private_fabric`
  [profile](authentication-profiles.md), any client that can reach
  `/api/v1/auth/self-issue` through the trusted front can mint a general
  contributor credential. A scoped participant token does not prevent that. The
  network and facade path of a browser architect must not reach that endpoint.
  Qualify this isolation before any browser production use.
- **Messages are data, never authority.** A message cannot expand a brief,
  approve a production action, impersonate a human or resume a paused task.
- **Pull only.** Delivery is pull-based for every client, including Codex,
  Claude Code, ChatGPT and Claude.ai. Claude.ai reads over direct MCP happen only
  during an active turn. A one-shot scheduled message can resume the same
  conversation after a turn ends, while recurring scheduled tasks start fresh
  sessions; both need qualification on the deployed client. Generic MCP SSE is
  not a wake-up guarantee.

## Client support today

- **HTTP** is the complete contract.
- Neither the bundled MCP server (`mcp/`) nor the read-only shim
  (`docplane-mcp-shim/`) exposes inbox tools. A deployment provides its own
  restricted facade.
- Push adapters (Claude Code channels, ChatGPT MCP events, a Codex continuation
  runner) are later work and are not built.
