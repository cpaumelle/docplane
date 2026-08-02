# 6 · Let it observe

Prometheus (or whatever you run) holds the readings; DocPlane holds the **meter list** — what is watched, what each rule means in plain English, and the evidence ledger that keeps the corpus honest. DocPlane must never become a monitoring system; it records what your monitoring *is*.

## Import your monitoring rules

If your alerting/recording rules live in git (they should), the shipped importer reconciles them into the model:

```bash
export DOCPLANE_API=http://localhost:8080
export DOCPLANE_METER_LIST_TOKEN=dp_...        # a named AUTOMATION token
export METER_RULES_DIR=/path/to/prometheus/rules
export METER_SOURCE_KEY=prometheus.main        # REQUIRED and stable — it is entity identity
python3 scripts/meter_list.py --dry-run        # see what would happen
python3 scripts/meter_list.py                  # reconcile
```

One run gives you, through the same governed contracts as everything else:

- a `MONITOR_RULE` card per rule, `WATCHES`-wired to the `SERVICE` card named by the rule's `service` label;
- generated plain-English explanation pages ("fires when disk usage stays above 85% for 15 minutes"), fingerprint-bound so editing a rule queues its explanation for regeneration;
- a declared artifact + a `GENERATION` observation carrying the rule-set fingerprint. An unchanged rule set publishes **nothing** — no churn, ever.

Schedule `scripts/run_meter_list_reconciliation.sh` (flock-guarded, same code path as manual runs) and the meter list stays continuously true. Full operator guide: [Meter-list importer](../operations/METER_LIST_IMPORTER.md).

## Coverage: the honest gap list

Open **observe · Coverage & evidence** (or `GET /api/v1/observe/coverage`):

- **Unwatched services** — cards with no rule watching them, ranked by the criticality of the pages describing them.
- **Rules without a plain-English description.**
- **Paging alerts without a runbook** — the gap that hurts at 3 a.m.
- **Unwired rules** — rules nobody can attribute to a service; fix the `service` label upstream (a versioned overlay exists for the stragglers, but labels win).

Every gap has a **Capture work** button — gaps feed the work inbox; the importer is structurally incapable of creating stub pages to paper over them. A bounded reconciler can also project gaps into reopenable work items (`POST /api/v1/observe/coverage/reconcile-work`) that converge run over run.

## Verification: pages checked against reality

Know pages decay silently unless something asks. A **verification request** is a work item scoped to a page or a path prefix ("verify `operations/proxmox/` before the upgrade"), carrying the page and its linked model cards as briefing:

- Trigger from **Review** (path-prefix pre-flight, ≤200 pages) or per page in Explore.
- Whoever picks it up — usually an agent — checks recorded facts against the live system. Facts hold → a verification recorded against that *exact revision*, evidence in the notes. Facts drifted → a correction through the normal change contract.
- Verification chips (`verified` / `unverified`) show everywhere pages are listed, and expiry decays stale verifications back into queues. Never a blind cron — triggers are on-demand, graph-ripple (a changed card flags every page `DESCRIBES`-wired to it), or expiry prompts.

## The evidence ledger

`POST /api/v1/observations` appends milestone evidence — deployed versions, generation fingerprints, soak readings, exercised-runbook attestations — with a `current_status` projection per subject, so "what does reality currently show for this card" is one indexed read (`GET /api/v1/model/entities/{id}/status`). Freshness and drift are *derived* by comparing fingerprints, never stored as authored claims. No time series; that's your monitoring stack's job.

Next: [Connect your agents →](07-connect-your-agents.md)
