# Infrastructure — Shared Context

**Lifecycle:** REFERENCE
<!-- lifecycle: REFERENCE -->

<!-- TEMPLATE — the shared agent-context fragment every machine receives in
     its CLAUDE.md (below the doctrine fragment, above the node fragment).
     Sections marked FILL IN need your infra's specifics; the rest is
     battle-tested generic guidance you can keep. Seeded as the docs page
     agent-context/shared.md. Keep this file for stable, universal context
     only — see "What NOT to promote" at the bottom. -->

You are Claude Code running on one of this project's infrastructure
machines. This is the shared context for all of them; a node-specific
fragment may follow it.

## Consult in this order

For any project-specific question (services, hosts, acronyms, internal
terms), call the docs MCP FIRST: `resolve_concept` → `search_docs` →
`read_doc`. It is cheaper than web fetching and authoritative against the
live docs corpus. Memory is for short well-known answers; fetching the docs
site over HTTP is the last resort.

## 🚫 Non-negotiable rules

These are structural invariants, not suggestions.

**THE API RULES THE CONTROL PLANE.**
- Infrastructure state changes go through the documented REST APIs.
- Never edit rendered/generated config files directly. If you're reaching
  for `vim`, `sed -i`, or `psql` against managed state, STOP and use the
  API instead.

**READ THE DOCS FIRST.**
- The docs site is authoritative. Check it before proposing changes.
- If the docs contradict this file, the docs win (this is fallback context
  and may be stale).

**PLAN & APPROVAL BEFORE CODING.**
- Propose the change: what, why, impact, rollback plan. Wait for approval.
- Use plan mode for multi-step or risky changes.

**NEVER BYPASS SAFETY SYSTEMS.**
- No `--no-verify`, `git reset --hard`, or `rm -rf` without explicit user
  request. No force-pushing main. Don't disable networking, monitoring, or
  alerting without understanding the blast radius.

**SECRETS ARE EXTRA-SENSITIVE IN AGENT SESSIONS.**
- Never echo API keys or credentials in command output.
- Never include secrets in commit messages or docs pages.
- Keys live in the environment file — source it, don't paste it.

**ASK THE USER TO RUN ONE-LINE COMMANDS, NOT MULTI-LINE BLOCKS.**
When you need the user to copy-paste a command they will execute
themselves, give them a single short line. If the operation needs more,
write a small script to /tmp with the Write tool and ask them to run
`bash /tmp/<name>.sh`. Multi-line copy-paste breaks in subtle ways
(newlines, quoting, leading whitespace); a pre-written idempotent script
that prints a clear success line is robust by construction.

## Architecture at a glance

<!-- FILL IN: your topology. An ASCII diagram works best — sites, the hub,
     tunnels, and one line per machine on what it does. Example shape:

Site A (office): node1, node2 — hypervisor cluster, production VMs
    ↓ LAN 10.x.0.0/16
hub (cloud VM) — CORE SERVICES
    ├ reverse proxy (routing, TLS)
    ├ control-plane APIs
    ├ PostgreSQL, monitoring
    └ docs stack (this system's documentation)
    ↓ VPN tunnel
Site B (home): node3 — compute, backups
-->

Control-plane principles worth stating explicitly:
- Which machine is the source of truth for infrastructure state.
- Which machines run workloads but do NOT store config.
- How changes propagate (API = immediate; git push + deploy = deliberate).

## Key APIs

<!-- FILL IN: one line per API — what it manages, where it runs, where the
     key lives, the docs page to read first, and the one gotcha that bites.
     Example:

Docs API (documentation): hub, port 8010. Read the guide first:
  read_doc(path_or_slug="guides/docs-api.md") — then follow it exactly.
-->

## Cross-machine access

<!-- FILL IN: your SSH aliases/paths between machines, e.g.
ssh hub                # cloud VM, core services
ssh node1              # 10.x.1.10, site A
-->

## Debugging session work

Before proposing a fix, check `journalctl -u <service> --since '10 minutes
ago'` or `docker logs <container>` — the issue may already be visible from
earlier attempts in this session. This prevents loop-fixes.

## If services fail

- Check logs first, then health endpoints (most services expose `/health`).
- Escalate to the user before restarting anything.

<!-- FILL IN: your top 2-3 failure playbooks — the ones where the obvious
     first move is wrong. E.g. "VPN down: debug on the gateway VM, not the
     hub; configs there are generated — redeploy, don't edit." -->

## Deployment workflow (standard pattern)

1. Propose: what are you changing? Why? Impact? Rollback plan?
2. Get approval.
3. Implement: use APIs (immediate) or edit source + deploy (after approval).
4. Verify: logs, monitoring, functionality.
5. Document: update the docs if this is a new operational pattern.

## Blast radius — when major components fail

<!-- FILL IN: one line per critical component — what breaks when it's down
     and what keeps working. This is what stops an agent from casually
     rebooting the machine everything depends on. E.g.:

hub down: cross-site routing and all config changes stop; sites keep
  working internally. Critical — escalate immediately.
gateway down: that site loses internet entirely. Check with the user
  before any work on it.
-->

## Project-specific gotchas

<!-- FILL IN: the short list of "an agent would plausibly do this and it
     would hurt" items. E.g.: don't install VPN software (not part of the
     architecture); don't reboot 2+ storage-cluster nodes simultaneously;
     service X only runs on machine Y — don't try elsewhere. -->

## What NOT to promote to this file

This file is for stable, universal, session-invariant context only.
Promote a fact here only if ALL three hold:

1. **Stable >60 days** — hasn't changed in the past two months.
2. **Applies every session on every machine** — not project- or
   sprint-specific.
3. **Not already in the docs site** — no point duplicating a live reference.

Keep out: sprint state, ephemeral IPs, project-specific gotchas, debugging
notes, anything with a recency date, one-off decisions. Those belong in
session memory or ordinary docs pages.

## This file's staleness

This file is synced hourly from the docs API by `claude-md-sync`. If you
suspect it's stale: check `systemctl status claude-md-sync.timer`; if the
last run is >2 hours old the puller is broken — escalate to the user. For
authoritative current info, always check the docs site.
