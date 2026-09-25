# Work-catalogue generator operator guide

`scripts/work_catalogue.py` renders one published page, `work/index.md` — a
browsable, read-only catalogue over live initiative state: queue counts,
Now/Roadmap/Blocked/Soaking/Parked sections, one anchored entry per open
initiative, and recently-completed with closure gates. It is the third instance of the
generated-artifact pattern, alongside the schema catalogue and the meter
list, and follows the same contract: deterministic rendering, a source
fingerprint over the rendered projection, UNCHANGED runs publish nothing
and replay one NOMINAL `GENERATION` observation, changed runs publish one
governed change and keep the artifact declaration in succession.

The site is never a second write path: the generated page links back to
the dashboard, and inbox captures appear as a **count only** — pre-triage
thoughts are not documentation.

## Many records, one page (projection contract 2)

WORK records are the authority; the catalogue is a survey of them, not their
history. Contract 2 (generator 2.0.0) replaced the page-per-initiative and
per-queue pages of contract 1 with the single catalogue page, so creating an
initiative never changes DocPlane page cardinality.

- **Stable reference:** `work/index.md#<initiative_key>`. Each entry's heading
  is the bare key, so the key is the anchor; queue sections are
  `#now`, `#roadmap`, `#blocked`, `#soaking`, `#parked`, `#recently-completed`.
- **Entry:** title, state, priority, the initiative UUID, target/review dates,
  blocker, soak or park details, links, the objective, the activity count and
  a one-line preview of the newest three activities.
- **History:** read from WORK, never from a page — MCP `docplane_work_get`
  with the entry's UUID (redacted bodies, paged with `activity_offset`), the
  REST `GET /api/v1/initiatives/{id}?activity_bodies=redacted`, or the
  dashboard.
- **Previews** reduce recorded links to their text and drop code fences.
  Stored activity bodies are never rewritten; they keep whatever paths they
  cited when written. Links in objective, blocker and soak text that point at
  a retired `initiatives/<key>.md` page render as the entry anchor.

The contract 1 → 2 transition is an ordinary succession: the first contract-2
run declares a successor owning only `work/index.md` and archives every page
the contract-1 declaration owned, in the same governed change. There are no
redirects; authored links into `work/*` are repointed to the anchors first.

## Required environment

```text
DOCPLANE_API=https://docplane.example.internal
DOCPLANE_WORK_CATALOGUE_TOKEN_FILE=/etc/charliehub/docplane-work-catalogue.token   # deployed; 0600 root:root
# DOCPLANE_WORK_CATALOGUE_TOKEN=<named AUTOMATION bearer>   # legacy plaintext delivery, removed 2026-09-23
```

Never print the bearer. Use the routed origin, not the direct API port. The bearer is delivered
by file through the SECRETS-V3 `_FILE` contract; see
[METER_LIST_IMPORTER.md](METER_LIST_IMPORTER.md) for the two-step migration this consumer also
followed, and for what the file contract does and does not buy.

## Running

```bash
python3 scripts/work_catalogue.py --dry-run   # render + report, zero writes
python3 scripts/work_catalogue.py             # reconcile
scripts/run_work_catalogue_reconciliation.sh  # flocked scheduler entrypoint + metrics
```

Production installs `config/systemd/docplane-work-catalogue.{service,timer}`.
The timer reconciles one minute after boot and one minute after each completed
run, with a small randomized delay. This bounds normal generated-view lag
without making the Work API a second writer for generated pages; steady-state
ticks are cheap no-ops by fingerprint. A closed
initiative's entry leaves the catalogue in the next regeneration; the page set
itself does not change.

Install the units from the pull-only deployment checkout, after placing the
named automation environment in `/etc/docplane/work-catalogue.env`:

```bash
install -o root -g root -m 0644 config/systemd/docplane-work-catalogue.service /etc/systemd/system/
install -o root -g root -m 0644 config/systemd/docplane-work-catalogue.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now docplane-work-catalogue.timer
systemctl start docplane-work-catalogue.service
```

The environment file must be root-owned mode `0600` and define only
`DOCPLANE_API` (the routed DocPlane origin) and
`DOCPLANE_WORK_CATALOGUE_TOKEN` (the named AUTOMATION bearer). Never place the
token in the unit, repository, command line, or journal.

The wrapper writes `docplane_generated_projection_*` metrics atomically to
the node-exporter textfile collector. It records live-versus-published drift,
the latest reconciliation result, and the latest check time. Override the
path with `DOCPLANE_WORK_CATALOGUE_METRICS_FILE` only when the node's canonical
textfile collector path differs.

The wrapper's lock defaults to `/run/lock/docplane-work-catalogue.lock`,
deliberately outside `/tmp`: a scheduler unit running with `PrivateTmp=true`
gets a private `/tmp`, so a lock there would exclude nothing from an
operator's concurrent manual run — the exact overlap the lock exists to
prevent. Override with `DOCPLANE_WORK_CATALOGUE_LOCK_FILE` only to a path
that every caller shares. When the lock is already held the wrapper reports
`SKIPPED`, still publishes drift metrics, and exits successfully: a skipped
overlap is the lock working, not a failed reconciliation, and must not raise
the failure alert.

## What it owns

- Exactly one page, `work/index.md` (`knowledge_class=REFERENCE`; it lands
  in the site's WORK nav group by path).
- One `SYSTEM` model card `docplane-work` as the artifact's source entity.
- One artifact declaration `work-catalogue`, retired and redeclared under
  the same key whenever the projection contract version moves; generator
  version and target set are reconciled in place.

## Failure behaviour

Publication failures follow the platform contract: authored state stays
durable, `POST /api/v1/publication/retry` resumes a failed publication of the
already-authored corpus. It does **not** re-project changed `work.*` source
state; the work-catalogue timer owns that reconciliation. All mutation calls
are fingerprint-keyed so a resumed run replays instead of double-acting.
The generator fails before any API call when the dedicated work-catalogue
token is absent; it never falls back to `DOCPLANE_TOKEN` or another principal.

If the declaring automation principal has been lost, an operator may transfer
custody without releasing page protection:

```text
POST /api/v1/bootstrap/model/artifacts/{artifact_id}/reassign-custody
X-DocPlane-Bootstrap-Token: <operator bootstrap credential>
Idempotency-Key: <unique logical mutation key>

{"expected_version": 1, "destination_principal_id": "<active AUTOMATION uuid>",
 "purpose": "Restore work-catalogue reconciliation under its named automation identity."}
```

The endpoint is CAS-bound, accepts only an active `AUTOMATION` destination,
and appends an audit event containing the old/new custody, purpose, and request
hash. It does not retire the artifact, alter its targets, or change page
protection.

## Independent source observation

The generated-artifact execution contract declares an independent, local
source observer with a nominal ten-minute cadence and a thirty-minute maximum
evidence age. Observation is not reconciliation: it reads the same canonical
WORK projection and records one `FRESHNESS_CHECK`, but it never renders,
publishes, reconciles MODEL, or treats detected drift as authority to generate.

The observer uses the existing named automation environment and the same
logical `work-catalogue` exclusion domain as generation. The deployed lock is
`/run/lock/docplane-work-catalogue.lock`. Expected nonblocking contention is a
benign skipped opportunity with no observation; identity, source-read,
canonicalisation, and OBSERVE-write failures remain visible as service
failures. The probe CLI creates a fresh UUID itself, so the unit does not carry
a second invocation-identity mechanism.

Install the observer unit files without enabling or starting them:

```bash
install -o root -g root -m 0644 config/systemd/docplane-work-catalogue-observer.service /etc/systemd/system/
install -o root -g root -m 0644 config/systemd/docplane-work-catalogue-observer.timer /etc/systemd/system/
systemctl daemon-reload
systemctl is-enabled docplane-work-catalogue-observer.timer  # expected: disabled
systemctl is-active docplane-work-catalogue-observer.timer   # expected: inactive
```

Installing and reloading are deliberately separate from activation. Merely
placing these files must not execute a source probe.

### Later activation gate

Enabling or starting the observer timer is a separate production gate. When
authorized, its timer waits two minutes after explicit activation and then runs
ten minutes after each completed opportunity, with at most thirty seconds each
of accuracy coalescing and randomized delay. It does not replay historical
missed ticks after downtime: one new current observation is the useful
evidence. This remains comfortably inside the declared 1,800-second evidence
bound and avoids deliberately aligning with the one-minute generation timer.

Successful journal output is the bounded `--status-json` receipt: probe ID,
source entity identity, fingerprint, observation ID, and outcome. The unit and
wrapper never print the bearer, headers, or environment, and write no second
persistent log. Recurring success writes OBSERVE only; it creates no WORK
activity and does not invoke generation.
