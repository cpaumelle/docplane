# Dashboard purpose and authority

The dashboard provides **human corpus observability, decision support and governed
authoring**. Its implementation owner is `dashboard/`, and its routed product surface is
`/dashboard/`.

The dashboard is an HTTP client of the DocPlane API. PostgreSQL, reached only through that
API, is authoritative. The dashboard has no database, filesystem, authentication,
permission, publication or release-store authority. It does not scan generated MkDocs
files or reconstruct corpus policy in browser JavaScript.

The retired legacy MkDocs dashboard is behavioral and design evidence only. Its useful
questions, visual hierarchy and calibrated structural signals inform the Corpus
Observatory; its implementation and any independent scanning model are not an authority.

## Product information architecture

The dashboard is verb-first: the sidebar groups its modules under the four domains
of intent, so the navigation itself teaches the model.

`Overview · WORK: Queues & inbox · KNOW: Explore / Review / Classify / Author ·
MODEL: Entities · OBSERVE: Coverage & evidence · Changes & versions`

- **Overview** is a campaign board, not a statistics page: unclassified pages,
  review candidates, work inbox, coverage gaps, maintenance attention and open
  changes, each card deep-linking into the view where that work happens.
- **Explore** navigates authoritative snapshot structure, pages and governed
  metadata, and is where reorganisation begins: selecting pages raises a move
  bar (target directory + stage). Staging compiles a governed reorganisation
  plan — resource IDs and revisions are bound from the snapshot, never typed.
  Its navigation organizer is the first-class structural editor: a searchable,
  collapsible tree with drag-and-drop, keyboard movement, undo/redo, explicit
  destination-URL review and staged impact analysis. Overview pages are visibly
  pinned first. The browser holds an unsaved draft only; persistence still runs
  through the reorganisation plan lifecycle.
- **Review** is the ranked attention queue. Structural candidates include bounded,
  server-generated reason codes, measured evidence, thresholds, resource IDs and
  revisions. Freshness and verification are Review concerns; the section
  path-prefix pre-flight and revision-bound verification request lifecycle live
  there. Staged reorganisation plans are inspected, analysed, validated and
  published here — the raw operation-payload form is gone; plans are authored
  from Explore's selection, and the plan detail renders each operation with its
  analysis verdict before publish.
- **Classify** is the reclassification workbench: a keyboard-driven queue
  (1–8 assign, j/k navigate) with a body preview, each assignment the same
  governed, optimistic-locked classify call used everywhere else. Its scope is
  selectable: `(missing)` burns down the classification audit's backfill;
  choosing a class reviews that class for accuracy section by section —
  re-affirming the current class is a skip, never a write.
- **Entities (model)** reads the card index: kind-filtered entity list, and an
  entity page aggregating attributes, typed wires (in and out), linked pages and
  the observe `current_status` projection with artifact freshness.
- **Coverage & evidence (observe)** leads with the coverage gaps — unwatched
  services ranked by criticality, rules without descriptions, paging alerts
  without runbooks, unwired rules — each capturable into the work inbox, plus
  open coverage work items and the recent observations ledger. The dashboard
  renders gaps; it never creates stubs.

Structural signals support contributor judgement; they never authorise mutation.
Classification and overdue policy have one owner in the maintenance API and its shared
server-side policy module. Observatory code consumes that policy rather than redefining it.
Protection and exclusion arrays are explicit in the response. They remain empty unless an
authoritative server-side governance contract publishes them; path names are never treated
as implicit protection policy.

The markdown-parsed lifecycle field is displayed only as **Legacy lifecycle signal**. It is
non-canonical pending metadata audit and backfill. Governed workspace, knowledge class,
publication state, archive state, verification state, provenance and criticality are the
durable classification surface.

## Bounded read contract

`GET /api/v1/dashboard/observatory` returns complete directory aggregates and paginated
review candidates. `GET /api/v1/dashboard/observatory/pages` returns page summaries.
Both paginated collections use the `docplane-named-after-v1` dialect:

- `count`: records returned in this response;
- `total`: records available in the snapshot;
- `has_more`: whether another page exists;
- `next_after`: opaque value passed to the matching `after` parameter.

Page responses default to 100 and permit at most 200 records. Path/title, identifier-family,
dated-file, archive-state and governed-classification filters are applied by the API to the
full snapshot before pagination. Candidate responses default to 50 and permit at most 200.

The authenticated export is produced by the API, not assembled by downloading page bodies
in the browser. Its manifest records resource IDs, paths and revisions. Export is limited
to 5,000 resources and 10 MiB uncompressed; crossing either limit returns
`413 EXPORT_LIMIT_EXCEEDED` with actual counts and configured limits. It never silently
returns a partial corpus.

## Governed authoring

A named contributor token is kept only in browser session storage and memory, then forwarded
to the API. Human authoring follows the same contract as MCP and other clients:

`SEARCH → READ EXACT REVISION → EDIT → VALIDATE → PUBLISH`

Review is optional and never blocks publication. The API owns revision checks, atomic mutation, version history, deployment certification and rollback.

## Private-fabric authentication bootstrap

The dashboard is a client of the existing DocPlane authentication contract; it does not own
an authentication or permission system. Discovery is authoritative for the active access
profile, acquisition mode, endpoint, method and operator procedure.

In `private_fabric`, routed fabric reachability is the admission boundary. The dashboard
automatically calls the credential-acquisition endpoint advertised by discovery, accepts the
server-generated short-lived `AGENT / CONTRIBUTOR` principal, validates it through ordinary
capabilities, and stores its clear bearer only in memory and `sessionStorage`. No name,
password, operator token handoff or approval is required. Cached bearers are validated before
reuse; a rejected bearer is cleared and replaced at most once through a single-flight
bootstrap.

In managed deployments, self-service is disabled. The dashboard does not attempt issuance;
it displays discovery's operator procedure and accepts the existing operator-issued bearer.
Manual bearer entry is a fallback, not the primary private-fabric flow.

Successful private-fabric bootstrap does not occupy navigation space with a Connected label
or token control. The principal appears unobtrusively in the footer. Managed-token-required
and bootstrap-failed states remain prominent, and explicit troubleshooting can reveal the
manual bearer fallback.

The routed DocPlane origin is security-relevant: it supplies the trusted fabric-admission
context for self-issue. Direct dashboard or docs-api service ports are not equivalent and
must not inject or weaken that admission. If bootstrap fails outside the routed front, the
dashboard directs the user to the routed URL or the manual fallback.

The overview is authenticated by design because it includes contributor and change-control
data. A blank overview is therefore not evidence that the corpus is empty. Check `/healthz`
for unauthenticated corpus counts, then authenticate and load the overview.
