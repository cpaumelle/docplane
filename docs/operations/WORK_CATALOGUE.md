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
```

Run it after meaningful work-state changes (transitions, closures), or on a
schedule; steady-state ticks are cheap no-ops by fingerprint. Initiative
pages for closed initiatives are archived automatically in the same change
that stops rendering them.

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
