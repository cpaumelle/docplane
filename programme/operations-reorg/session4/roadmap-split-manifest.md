# invariant-roadmap.md split — content-conservation manifest (docplane#108)

*2026-07-31. Governed change `56918b3b-26c5-4d53-af97-2afcd79d49e1` (rehearsed
`61eb700d`, validated, published). Certification CURRENT, working==deployed, 0 open
changes after publish.*

## Source

`operations/invariant-roadmap.md` (`6d91f9ba-eba5-5bea-9b30-7afed9c603e6`),
pre-split revision `b88d0b3e-601e-4e67-b6eb-a0883058a67f`, version 49, 52762 bytes.

## Durable spans extracted (verbatim, exact character offsets against source content)

| Section | Span (char offset) | Length | sha256 (16) |
|---|---:|---:|---|
| When to add a new invariant | 34521–35133 | 611 | `ead57a56e6e12138` |
| Explicit non-goals | 50702–51239 | 536 | `f7712c620c63b07e` |
| Design principles | 51239–51802 | 562 | `b79d6d7aec81d513` |
| Maturity model | 51802–52233 | 430 | `7c1bd96f211e2f78` |

No span overlaps (verified programmatically). Each block's exact post-heading text
verified present, byte-for-byte, in the new page before publish (verbatim-inclusion
check).

## Destination

**New canonical page**: `control-plane/foundational/invariant-governance.md`
(CREATE_PAGE). Contains the 4 blocks above verbatim, joined by section dividers and a
short non-content preamble noting the extraction and pointing back to the status page.
Chosen destination: `control-plane/foundational/` already holds the corpus's other
doctrine/methodology pages (`enforcement-model.md`, `doctrine.md`,
`observe-calibrate-enforce.md`, etc.) and has no existing page covering
invariant-justification methodology specifically — genuine IA gap, not an
existing-page merge candidate. No new directory created.

## Historical/planning content disposition

**Retained at its current path** (`operations/invariant-roadmap.md`, no MOVE_PAGE) —
per #108's own text, this content ("Current state" live-enforcement table, "Filed but
not yet implemented" backlog table, Phase 0–5 tracking, evidence appendices) is a
dated status/backlog record, not provably dead: several "Filed but not yet
implemented" items may still be genuinely open work, and this split did not attempt to
re-verify each one's current shipped state against the live corpus (out of scope for a
placement decision). Reducing it to a historical pointer, as #108 allowed as one
option, was rejected here specifically because that would risk silently losing live
backlog visibility without verification — the "stay in operations with a revised title
and lifecycle" option was chosen instead, since it requires no unverified claim about
any individual item's status.

**Changes to the retained page**: title → "CharlieHub Control Plane — Invariant
Enforcement Status & Backlog" (was "... Invariant Roadmap" — the old title implied
forward-looking planning only; the page is now purely status/backlog/history);
`nav_path` → `Operations/Invariant Enforcement Status & Backlog`; lifecycle marker
REFERENCE → BACKLOG (this page is not stable reference material — it is dominated by
unshipped backlog items); "Last updated" stamp appended with a note pointing to the
split and explicitly disclaiming re-verification of the status/backlog content itself.
A single consolidated pointer section ("Durable methodology has moved") was inserted
at the position the first extracted section previously occupied.

## Content-conservation proof

Verified programmatically before publish: `modified_page_content` reduces to exactly
`original_content` with the 4 durable spans removed, once the pointer section and the
title/lifecycle/timestamp header edits are reverted — i.e. **every byte of the
original that isn't one of the 4 durable spans is present, unchanged, in the retained
page**, and every byte of the 4 spans is present, unchanged, in the new page. No
content was rewritten, summarized, or dropped; the only added text is the pointer
section and the header/footer metadata edits listed above, all of which are
connective/administrative, not substantive.

## Inbound-reference treatment

3 corpus-wide inbound references to `operations/invariant-roadmap.md`
(`control-plane/design/lw-r1-design-notes.md`, `control-plane/invariant-catalog-1.md`,
`operations/index.md`) — checked for anchor/fragment targeting into the 4 removed
sections: **none found**; all 3 are plain page-level links, unaffected by the path
staying the same. `operations/index.md`'s catalogue link text updated in the same
change to match the new title (bounded reference repair, bundled atomically).

## Canonical-authority decision

`control-plane/foundational/invariant-governance.md` is the sole canonical source for
the 4 durable sections after publish — no other page in the corpus contains this
content. `operations/invariant-roadmap.md` no longer claims authority over it; its
pointer section directs there instead.

## Verification

Certification: CURRENT, working==deployed, 0 open changes. Both routes (old path
`operations/invariant-roadmap/`, new page
`control-plane/foundational/invariant-governance/`) return 200.
