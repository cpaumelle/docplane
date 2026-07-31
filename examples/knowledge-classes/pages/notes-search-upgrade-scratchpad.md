# Search upgrade scratchpad

**Lifecycle:** ACTIVE

Working state for the search-relevance investigation. This page is a
scratchpad: it changes daily while the work is live and gets archived
when the work closes. Durable conclusions move to real pages before
archival — nothing load-bearing may live only here.

## Current status

Comparing the built-in index against a trigram approach on the staging
corpus. Trigram wins on typo tolerance, loses on index size (about 3×).

## Open threads

- [ ] Measure query latency at 1k pages, not just 200
- [ ] Does the index rebuild fit inside the publication transaction, or
      does it need to be async with a freshness marker?
- [x] Confirm search only ever reads the generated release, never drafts

## Raw numbers (staging, 200 pages)

| Approach | p50   | p95   | index size |
|----------|-------|-------|------------|
| built-in | 12 ms | 40 ms | 2.1 MB     |
| trigram  | 8 ms  | 22 ms | 6.4 MB     |

## When this closes

Write the comparison up as a DESIGN page, raise an ADR if we switch,
archive this scratchpad.
