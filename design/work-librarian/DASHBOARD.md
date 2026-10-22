# The dashboard as a window into the librarian's work

Status: proposed product and integration contract. No runtime change or deployment.

The home page should answer: **What needs my attention, what does the librarian
recommend, and how much of WORK has it actually examined?** The current campaign
counters, certification panel and catalogue regeneration feed cannot answer those
questions. Keep the existing KNOW, MODEL, OBSERVE and publication tools available;
make the librarian's work the primary landing experience.

## Home: Work briefing

Use the existing SPA and its authentication, visual tokens and navigation. Add
four WORK views: Briefing, Backlog, Proposals and Runs. Retain Queues & inbox for
direct capture and triage. Move certification and publication events to the
existing Changes & versions view rather than presenting them as work progress.

The home page, in order:

1. **Attention now.** Important unresolved concerns, decisions needed and complete
   next actions. Each item names the work, why it matters, the source evidence and
   the next person or verification needed. Render complete sentences. Concerns
   have their own section: they must remain visible even when no action has been
   extracted. A display cap links to the full list and states what was omitted.
2. **Backlog by workstream.** Outstanding obligations grouped under existing or
   candidate homes, with a separate group for work without a home. Show completed,
   deferred and uncertain currency distinctly. A capture's filing lifecycle never
   determines whether its work is complete.
3. **Decisions and proposals.** A short queue of suggestions needing review, with
   held and provisional decisions explicitly labelled. Separate reviewed proposals
   from unreviewed output. Opening a suggestion explains the proposed change,
   target, evidence, uncertainty and the reason it is held.
4. **Coverage.** Distinguish inventoried, evaluated, uncertain and unevaluated
   inputs. Show records opened as search evidence separately. For example, opening
   123 captures while evaluating 20 does not mean 123 captures have been evaluated.
   Show KNOW and initiative-history coverage, failed reads and search omissions.
5. **Latest run and review.** Collection window, cutoff, release, review verdict
   and link to its preserved output. Indicate live collection versus a frozen
   snapshot. An incomplete run is labelled before its conclusions are shown.

Every section has loading, unavailable, empty and error states. Missing results
mean "No librarian report published", never zero outstanding work or complete
coverage. A report's figures carry its timestamp; live INBOX counts carry theirs.
Do not silently subtract counts collected at different times.

## Backlog and proposal detail

Backlog filters: workstream, open/completed/deferred/uncertain, tracked/candidate/
no home, reviewer status and source run. Search titles and explanations. Open an
obligation to see its stable ID, complete action and acceptance, each source's
scope, later evidence, candidate or canonical home and related concerns. Link to
the underlying WORK record through existing authorised routes.

Proposal filters: needs review, held, approved, partly applied, applied and
rejected/withdrawn if the source ledger supports those states. These labels are
not new execution authority. Never infer that a pending proposal is approved or
executable. Show operator holds independently of dependency holds. If the source
cannot represent a hold, disclose that gap and withhold a "ready" presentation.

For multi-capture proposals show each subject separately, including its receipt
or hold. A partly applied filing cannot make the remaining captures appear ready.
Preserve earlier decisions when displaying a newer run. The dashboard consumes
the ledger; it must not implement a second ledger or recompute decisions.

Evidence links open a readable source preview with record ID, revision/time and
the cited excerpt. Keep canonical recorded facts separate from the librarian's
interpretation and from unverified host/GitHub claims. A valid structural check
and an independent semantic review are separate badges, with review coverage and
limitations available in detail. Derived corrections are visibly distinguished
from the first output and link back to it.

## Data integration: the missing part

Today the librarian's sealed output and generated ledger live on VM8555. They are
not exposed by the DocPlane API. The SPA must not scan that filesystem, scrape
chat reports, require Charles to upload JSON, or hardcode the counts from a test.

Publish a deliberately bounded display projection through the DocPlane API.
Retain the existing authority model in `dashboard/CONTROL_PLANE.md`: the dashboard
is an authenticated API client with no separate database or filesystem authority.

The first increment needs:

- A versioned display schema derived from validated interpretation, evaluations,
  combined ledger, coverage and run config. Include stable IDs, holds, per-subject
  application state and receipts, evidence, provenance and explicit omissions.
- An explicit attended publication step for the display report, independent of
  filing WORK captures. Bind the report to exact source bundle hashes. Verify the
  source manifest and validation before accepting it; retain immutable versions
  and make publication idempotent. Publishing a report never applies a proposal.
- API-owned persistence and authorised read endpoints for the current report,
  bounded/paginated obligations and proposals, run history and report details.
  Scope these endpoints with WORK read permissions; protect report publication
  with an explicit separate permission. Reject unknown schema versions.
- Reviewer metadata supplied by the coordinator as a separate attestation bound
  to the exact output hash. An interpreting agent cannot mark its own output as
  independently accepted. Do not import sealed reference answers.
- A current-report pointer chosen explicitly. A newer incomplete or unreviewed
  run must not silently replace the reviewed combined view. Retain both current
  reviewed results and newer output awaiting review.

Only approved display fields are published. Exclude credentials, raw tool logs,
private paths, prompts and sealed expectations. Source previews continue through
normal API authorisation. Use plain text or sanitised rendering for report text;
never inject agent-provided HTML. The report is an advisory projection, not a new
canonical source of WORK lifecycle or initiative state.

## Delivery and acceptance

1. Define and test the display projection against synthetic examples and the
   supported worker shapes. Fix held-decision loss in its owning ledger first;
   the projection must not conceal it. No new interpreting run is needed.
2. Add API persistence, explicit report publication and authorised read routes,
   then the SPA Briefing, Backlog, Proposals and Runs views. Preserve existing
   tools and deep links. No scheduling, filing or bulk-action controls in this
   first increment.
3. Rehearse with a derived report from an accepted run. Verify desktop and narrow
   mobile navigation, keyboard access, complete action text, evidence links,
   pagination, and unavailable/error states. Check that agent text cannot execute
   markup and that unauthorised users cannot read or publish reports.
4. Demonstrate the first reviewed production report after a separately approved
   deployment. Show actual coverage and holds; never present an unevaluated corpus
   as reorganised. A report publication must leave captures and initiatives
   unchanged. A filing receipt can subsequently update a new display version.

The success test is practical: Charles can open the dashboard and identify the
next decision, the outstanding work, why the librarian proposed each placement,
what is on hold and how much remains unexamined, without reading an agent report
or opening JSON.
