# CLAUDE.md distribution — agent context as managed docs pages

If you run coding agents (Claude Code etc.) on more than one machine, each
machine's `CLAUDE.md` context file becomes state that rots: copies drift,
nobody remembers which box has which version, and there's no history of who
changed what.

The pattern: **store the agent context as ordinary pages in the docs
database**, and let each machine pull and compose its own `CLAUDE.md` on a
timer. You get versioning, rollback, audit labels and a single write path
(the same API and MCP tools you already use for docs) for free.

## How it works

```
docs DB pages:
  agent-context/doctrine.md          ← engineering doctrine / agent rules (optional)
  agent-context/shared.md            ← context every machine gets (required)
  agent-context/nodes/<hostname>.md  ← per-machine context (optional)

each machine, hourly (systemd timer):
  claude-md-sync
    ├─ GET the pages from the docs API
    ├─ sanity-check them (must start with an H1 — rejects error pages)
    ├─ compose: header warning + doctrine + node + shared + footer
    └─ atomically install as ~/.claude/CLAUDE.md, chmod 444 (read-only)
```

Ready-to-adapt starting points for the doctrine and shared fragments live in
[`templates/`](templates/) — a generic engineering-doctrine page (usable
nearly as-is for any control-plane-driven infra) and a shared-context
template with FILL-IN markers for your topology, APIs, blast radius, and
gotchas. `scripts/seed-example-docs.sh` publishes both into the docs DB.

The installed file is read-only on purpose: the header says exactly where to
edit instead, so an agent (or human) who tries to "fix" the local file gets
redirected to the control plane. Fetch failures keep the previous file in
place (stale is better than missing).

## Setup

1. Create the pages. The seed script already published the doctrine and
   shared templates (`agent-context/doctrine.md`, `agent-context/shared.md`)
   — fill in their FILL-IN sections via the API or the `write_doc` MCP tool.
   Then add a node page per machine:

```bash
source ../.env
curl -sf -X PUT "$DOCS_API_PUBLIC_URL/api/docs/pages/agent-context/nodes/$(hostname -s).md" \
  -H "X-API-Key: $DOCS_API_KEY" -H "Content-Type: application/json" \
  -d "{\"title\":\"$(hostname -s) Context\",\"nav_path\":\"Agents/Nodes/$(hostname -s)\",\"content\":\"# $(hostname -s) — Node-Local Context\n\nWhat runs here, where, and the local footguns.\n\"}"
```

2. Install the sync script on each machine:

```bash
sudo install -m 755 claude-md-sync /usr/local/bin/
sudo install -m 644 claude-md-sync.service claude-md-sync.timer /etc/systemd/system/
sudo mkdir -p /etc/docs-stack
printf 'DOCS_API_BASE=%s\nDOCS_API_KEY=%s\n' "https://your-docs-api.example.com" "<key>" \
  | sudo tee /etc/docs-stack/claude-md-sync.env >/dev/null
sudo chmod 600 /etc/docs-stack/claude-md-sync.env
sudo systemctl daemon-reload
sudo systemctl enable --now claude-md-sync.timer
sudo systemctl start claude-md-sync.service   # first run now, don't wait an hour
```

3. Edit context via the API (or the `write_doc` MCP tool from inside a Claude
   session — agents maintaining their own context file, with history).

Every knob in the script is env-overridable (`CLAUDE_FILE`, `SHARED_PAGE`,
`NODE_PAGE`, `STAGING_DIR`, …) — see the header of `claude-md-sync`. On
macOS, override the paths to user-writable locations and wrap it in a
launchd plist instead of systemd.

## Design notes (learned the hard way upstream)

- **Sentinel validation before install** — an HTML error page or empty body
  must never become your agents' context file.
- **Atomic install** (write staging file, `mv` over) — no half-written file
  if the machine dies mid-sync.
- **Soft-fail per fragment** — if only the node page is missing, you still
  get the shared context; if a fetch fails, yesterday's file survives.
- **The header/footer in the installed file must point at the edit path.**
  Without it, someone WILL edit the file directly and lose the change an
  hour later.
