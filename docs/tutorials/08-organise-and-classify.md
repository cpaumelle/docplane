# 8 · Organise and classify

A corpus that grows is a corpus that drifts — pages land in the wrong place, classes go stale, sections silt up. DocPlane treats structural change as a governed activity with its own tooling, so reorganisation is routine maintenance instead of a dreaded migration.

## The Classify workbench

**know · Classify** is a keyboard-driven queue over the classification audit:

- **Scope `(missing)`** burns down pages with no `knowledge_class` — a per-section burn-down shows exactly where the debt is and when a section hits zero.
- **Scope = a class** reviews that class for *accuracy*, section by section. Bulk backfills tend to default everything to one class; review mode is how you pay that down. Re-affirming the current class is a skip, never a write.

Keys: `1`–`8` assign, `j`/`k` navigate, `s` skip. Each row shows the page body inline; every assignment is the same optimistic-locked classify call used everywhere else, recorded with a reason.

At scale, let an agent propose first: `scripts/knowledge_class_suggest.py` emits names-only suggestions (no writes), you curate the JSON, and `scripts/knowledge_class_apply.py` applies them through the governed verb in bounded batches. The example corpus (`examples/knowledge-classes/`) demonstrates the whole loop.

## The Review queue

**know · Review** ranks structural attention candidates from the live corpus snapshot: oversized flat directories, repeated filename stems, missing section landings, dated-file buildups. Each candidate carries machine-readable reason codes and evidence counts — and four actions: **Inspect** (drill Explore to that directory), **Verify scope** (fabric verification pre-flight), **Capture work**, or stage a move.

## Moving pages: select, target, stage

Reorganisation is deliberately *not* a raw file operation:

1. In **Explore**, tick the pages that belong elsewhere.
2. The selection bar appears: pick a target directory, **Stage move plan**.
3. DocPlane compiles a plan of `MOVE_PAGE` operations — resource IDs and revisions bound from the snapshot, redirects created by default — and analyzes it.
4. In **Review**, the plan shows each operation with its analysis verdict (target collisions, revision staleness). **Analyze → Validate → Publish** when clean.

Publication is atomic: moves, redirects and navigation apply together, priors are archived, the site rebuilds, certification records the release. Old routes 301 to new ones, so inbound links keep working while you repair them properly (`scripts/docplane_links.py` plans and applies bounded link rewrites around moves, dry-run by default).

## Archive, don't delete

Retiring content is a state, not a removal: `ARCHIVE_PAGE` keeps the page and its history, out of active navigation and search defaults, restorable with `RESTORE_PAGE`. Ledgers (`decisions/`, incidents, postmortems) are *supposed* to grow — archive is for content that was superseded, not content that got old.

## A rhythm that keeps structure healthy

- **Weekly, five minutes:** glance at Overview — unclassified count, top review candidate, coverage gaps. Capture anything that needs a decision.
- **When a section hits ~15 direct pages:** check Review; it has usually already noticed and can say why.
- **After any batch of moves:** run the link scanner, and let redirects carry stragglers in the meantime.
- **Never** create "temporary" structure outside the plan flow. Temporary becomes permanent; the plan receipts are what keep structure auditable.

---

That's the full loop: install, author, model, work, observe, agents, organise. From here the corpus grows the way the philosophy intends — work births truth, closure pushes it into know/model/observe, verification checks it against reality, and structure stays governed. Further depth: [the four-domain model](../architecture/DOMAIN_MODEL.md) and [guiding philosophy](../architecture/GUIDING_PHILOSOPHY.md).
