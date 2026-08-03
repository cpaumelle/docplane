# Work-catalogue generator operator guide

`scripts/work_catalogue.py` renders the published `work/` section — a
browsable, read-only surface over live initiative state (queue board,
Now/Roadmap/Blocked/Soaking/Parked pages, recently-completed with closure
gates, one page per open initiative). It is the third instance of the
generated-artifact pattern, alongside the schema catalogue and the meter
list, and follows the same contract: deterministic rendering, a source
fingerprint over the rendered projection, UNCHANGED runs publish nothing
and replay one NOMINAL `GENERATION` observation, changed runs publish one
governed change and keep the artifact declaration in succession.

The site is never a second write path: every generated page links back to
the dashboard, and inbox captures appear as a **count only** — pre-triage
thoughts are not documentation.

## Required environment

```text
DOCPLANE_API=https://docplane.example.internal
DOCPLANE_WORK_CATALOGUE_TOKEN=<named AUTOMATION bearer>
```

Never print the bearer. Use the routed origin, not the direct API port.

## Running

```bash
python3 scripts/work_catalogue.py --dry-run   # render + report, zero writes
python3 scripts/work_catalogue.py             # reconcile
scripts/run_work_catalogue_reconciliation.sh  # flocked scheduler entrypoint + metrics
```

Run it after meaningful work-state changes (transitions, closures), or on a
schedule; steady-state ticks are cheap no-ops by fingerprint. Initiative
pages for closed initiatives are archived automatically in the same change
that stops rendering them.

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

- Pages under `work/` (`knowledge_class=REFERENCE`; the section lands in the
  site's WORK nav group by path).
- One `SYSTEM` model card `docplane-work` as the artifact's source entity.
- One artifact declaration `work-catalogue`, retired and redeclared under
  the same key whenever the target page set or generator version moves.

## Failure behaviour

Publication failures follow the platform contract: authored state stays
durable, `POST /api/v1/publication/retry` resumes, and all mutation calls
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

The audit event is `MODEL_ARTIFACT_CUSTODY_REASSIGNED` on channel `API` with
`producer_id = docplane-bootstrap`. Break-glass authority is carried by that
producer and event type, not by a channel value — the channel vocabulary is
closed by the genesis CHECK and `EventIngest.channel`, so listing every
break-glass action is a `producer_id` query. Because the event is appended
inside the custody transaction, an unlistable channel would roll the whole
reassignment back rather than record it silently.
