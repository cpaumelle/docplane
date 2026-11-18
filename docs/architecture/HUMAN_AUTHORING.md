# Human authoring

The dashboard is a first-class client of the DocPlane API, not a second
authority. It owns no document or work state.

## Pages

A person searches for the canonical page, loads its exact current revision,
edits lossless Markdown, inspects the diff, validates the candidate and
publishes it. There is no approval gate and no direct database or filesystem
save path.

The browser forwards the person's named contributor token. PostgreSQL records
the author, change, operations, prior page versions and audit events. Generated
Markdown and MkDocs output are disposable release artifacts. A stale edit is
rejected and must be rebased. A prior version can be restored through the same
validated publication path.

Navigation order and page moves are authored in the dashboard's organiser; see
[Reorganisation control plane](REORGANISATION_CONTROL_PLANE.md).

## Work

The dashboard opens on the **Work board**
(`dashboard/static/index.html`, `dashboard/static/app.js`):

- choose an initiative and read its [brief](WORK_BRIEF.md): purpose, next
  actions, holds, deferrals and recent evidence;
- add or update one named item through a labelled form, which records a named
  update bound to the brief you read;
- triage the capture inbox: promote, attach or discard.

People and agents see the same items, so a person reviewing progress reads what
the agents recorded rather than a relayed summary. Notes never change items or
release holds by themselves.

## Model and observe

The dashboard also shows model entities and the observe coverage and evidence
views. These are read and review surfaces over the same API.
