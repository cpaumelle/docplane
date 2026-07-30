# Agent 44 — prepared next flat-control-plane batch

**Status: PREPARATION ONLY.** No live state touched; publication lane not held. All
membership below is **provisional** and must be regenerated from a fresh snapshot
before any rehearsal — two of these pages sit next to Agent 45's in-flight
13-page topology component.

Snapshot: 2026-07-30, 514 active pages, 20 flat-root `control-plane/*.md` remaining
in Agent 44 territory (`ccm-*` excluded — Agent 45).

## The easy work is finished

Every remaining page needs a decision. Nothing is left that is merely misplaced.

| Class | Count | Pages |
|---|---|---|
| hub (inbound ≥ 10) | 5 | `sd-wan-invariants` 87, `sdwan` 36, `ccm` 15, `cp-net-1-driver` 13, `edge-identity-model` 12 |
| lifecycle excluded | 5 | `cleanup-domains-net-internal`, `invariant-catalog-1`, `invariant-catalogue-parity-triage` (BACKLOG); `cp-net-1-history`, `docs-mcp` (ARCHIVED) |
| self-deprecating | 2 | `system-map`, `current-state` |
| navigation / crosswalk | 3 | `architecture-overview`, `hub2-authority-crosswalk`, `gateway-runtime-responsibilities` |
| cross-subtree service doc | 2 | `charliehub-mcp`, `docplane` |
| policy registry | 1 | `class-b-hosts` |
| completed programme record | 1 | `site-network-authority` |
| correctly placed | 1 | `index.md` (section landing) |

## Batch A — nav/path mismatch, destination already populated

**Blocked on one decision, not on evidence.** Both pages' navigation *already asserts*
a destination that exists and has peers; only the inbound-count hub proxy stops them.
Both have **zero nav children**, so neither is a landing page.

| Page | Stable ID | nav says | destination | inbound | nav children |
|---|---|---|---|---|---|
| `ccm.md` | `239bd178-4a5d-5dfd-90ab-1fd69a2f2380` | `Control Plane/Foundational/CCM Contract` | `control-plane/foundational/` (11 peers) | 15 | **0** |
| `cp-net-1-driver.md` | `1aead82e-9c88-5f39-b059-348d2026a9c0` | `Control Plane/CP-NET-1/Driver` | `control-plane/cp-net-1/` (exists) | 13 | **0** |

**Decision required:** the hub gate fires on inbound count, but the structural test
(nav children, outbound shape) says both are heavily-cited *leaves*. This is the same
question already referred for `filesystem-hardening` (inbound 14, zero nav children).
One ruling settles all three. I am not self-approving it inside a publication.

Note `ccm.md` is adjacent to Agent 45's `ccm-*` territory by name but is **not** a
`ccm-*` page; ownership must be confirmed by stable ID before it moves.

## Batch B — cross-subtree service documentation

Well precedented: `services/` has 44 direct children including `docs.md`,
`transit-manager.md`, `edge-agent.md`, `authelia.md`.

| Page | Stable ID | inbound | evidence |
|---|---|---|---|
| `charliehub-mcp.md` | `c2a0e0d9-b82d-5630-8482-4a7beb87edef` | 5 | container, FQDN, auth token, transport, network tier, per-backend tool inventory |
| `docplane.md` | `1a6f8e5c-…` | 1 | states "This page is the fabric deployment reference"; components, discovery URLs, deploy & rollback, health checks |

**Confirmation required:** these leave the claimed flat-`control-plane` territory. I
will not take a cross-subtree move unilaterally.

## Not proposed, with reasons

- **`architecture-overview` / `hub2-authority-crosswalk`** — both have **inbound 0 and
  outbound 0**. Every link they carry is absolute or already broken; nothing in the
  corpus points at them. Two navigation pages that nobody navigates from or to is a
  lifecycle question, not a placement one.
- **`system-map` / `current-state`** — both open by telling readers not to rely on them.
- **`sd-wan-invariants` (87 inbound)** — the most-cited page in the corpus. Any move is
  a hub design with real reader impact.
- **`class-b-hosts`** — a live exemption *register* with per-host rows and an audit
  trail. Registers have no established home; inventing one for a single page would be
  inventing a directory.

## Preflight when the lane frees

1. Pull the shared branch; confirm Agent 45's 13-page component landed and read its receipts.
2. Regenerate membership from a fresh snapshot — **these IDs are provisional**.
3. Re-run the corrected existence gate as the pre-publication baseline.
4. Batch A only after the hub ruling; Batch B only after cross-subtree confirmation.
5. Convert any relative sibling links to root-absolute **in the same publication** —
   both sibling breaks repaired this session came from not doing that.
