# Corpus link tooling

Reorganising the corpus moves pages between paths. Every move invalidates the
references that point at the old path, and repairing them is the step most likely to
cause collateral damage: a careless rewrite edits prose, a careless parser misses
references, and **both failures look like success**.

`migration/links.py` is the deterministic, executable form of the rules that survived
several real migrations. `scripts/docplane_links.py` is its command-line surface.

## Boundaries

This tooling is deliberately narrow. Four concerns are kept apart because conflating
them is how a migration acquires a second, drifting source of truth:

| Concern | Owner |
|---|---|
| Link discovery, resolution, rewriting, route derivation | `migration/links.py` (this tooling) |
| Which pages move, and why | a reviewed cohort manifest, outside this repo |
| Publication, certification, receipts | the Docs API |
| Whether a page is eligible to move at all | the live coordination register that governs it |

`migration/links.py` performs **no I/O**: no network, no clock, no global state. A
caller supplies a corpus mapping of `path -> markdown`. That is what makes it usable
from CI, from an operator shell and from a test without a live instance — and what
stops it from quietly becoming a second authority on corpus state.

The CLI operates on a corpus **snapshot** (a directory of Markdown, or a JSON mapping)
for the same reason. It never publishes.

## The four defects this encodes

Each reached production. The regression suite names them so they cannot be optimised
away as redundant.

**(a) Line-oriented parsing under-reports references.** A Markdown link may span a line
break, its label on one line and its destination on the next. A per-line parser silently
skips such links, leaving a stale reference behind while every check still reports
success. All matching runs over the whole document.

**(b) A rewrite can change more than it claims.** `mask_links` replaces every link
*destination* with a constant. If the masked before and after are byte-identical, then
nothing outside link destinations changed — prose, headings, anchors, labels and
whitespace are all intact. `plan_rewrites` **refuses** to emit a body that fails this,
so the property is proven per document rather than asserted once.

**(c) A navigation label derived from a title corrupts the tree.** A title is prose and
may contain the navigation separator, which silently invents an extra navigation level
and discards a curated label. Use `retarget_nav`, which takes the leaf from the existing
navigation path; `nav_path_from_leaf` rejects any leaf containing a separator, so a
title can never be used as one.

**(d) An expected path hardcoded to one migration's destination.** A verifier that looks
for a literal directory passes for the cohort it was written against and false-fails
every other. `expected_redirect_hop` derives the expected relative hop from source and
target depth.

## Preservation is explicit, not a silent skip

Not every reference should be rewritten. Fenced examples, quoted material and evidence
surfaces record a path *deliberately*, and rewriting them falsifies the record.

Those references are **preserved** and classified (`PreservationReason`:
`fenced_code`, `blockquote`, `evidence_surface`). Preservation is reported, never
silent, so a later scan can reconcile **exactly** against what a rewrite declared —
`docplane_links.py scan --expect-preserved` fails when the two disagree in either
direction.

A preserved reference is not a defect when a compatibility route serves the old path.

## Failing closed

The tooling raises rather than guessing:

- `ParserUncertaintyError` — an unterminated code fence. The rest of the document could
  be code or prose, and the answer changes which references are protected.
- `AmbiguousIdentifierError` — an identifier prefix that does not uniquely identify one
  resource. Matching on a truncated identifier is only sound while prefixes are unique,
  and that is a property of the data, not a guarantee, so it is proven per run.
- `RuntimeError` — a rewrite that would change more than link destinations.

## Command line

All mutating operations default to a dry run. Every subcommand can emit a deterministic
JSON receipt with `--json`, suitable for CI assertions and for attaching to publication
evidence.

```bash
# What would a set of moves imply? (read-only)
python scripts/docplane_links.py plan --corpus ./corpus --manifest moves.json --json -

# Dry run, then apply
python scripts/docplane_links.py apply --corpus ./corpus --manifest moves.json
python scripts/docplane_links.py apply --corpus ./corpus --manifest moves.json --write

# What compatibility hop should each old path serve? (derived from depth)
python scripts/docplane_links.py routes --manifest moves.json

# Any stale references left? Exit 1 if unintended ones remain.
python scripts/docplane_links.py scan --corpus ./corpus --manifest moves.json \
    --expect-preserved declared-preservations.json
```

Manifest format — extra keys are ignored, so a richer cohort manifest can be passed
directly:

```json
{"moves": [{"from_path": "a/b.md", "to_path": "a/c/b.md"}]}
```

Exit codes: `0` success, `1` a violation was found, `2` usage or input error.

## Tests

`migration/tests/test_links.py`. CI runs it path-scoped via
`.github/workflows/corpus-link-tooling.yml`, which additionally asserts that the four
historical-defect cases ran — renaming or deleting one would otherwise silently remove
the only guard against that defect.
