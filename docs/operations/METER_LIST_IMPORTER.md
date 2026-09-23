# Meter-list importer operator guide

The Prometheus rule files in Git are authoritative. `scripts/meter_list.py`
is their sole DocPlane reconciler: it parses, reconciles Model, publishes Know,
declares the generated artifact and records Observe evidence. The coverage
endpoint derives gaps from that model; `work.coverage_gap_items` is only its
bounded triage projection.

Nothing in this workflow creates a runbook. Missing paging runbooks are work
to triage; runbooks themselves are born from real events under the runbook
discipline in `DOMAIN_MODEL.md`.

## Required environment

```text
DOCPLANE_API=https://docplane.example.internal
DOCPLANE_METER_LIST_TOKEN_FILE=/etc/charliehub/docplane-meter-list.token   # deployed; 0600 root:root
# DOCPLANE_METER_LIST_TOKEN=<named AUTOMATION bearer>   # legacy plaintext delivery, removed 2026-09-23
METER_RULES_DIR=/srv/monitoring/prometheus/rules
METER_SOURCE_KEY=hub2.prometheus   # required; production identity — never guess or substitute
METER_SERVICE_MAP=/srv/docplane/config/meter-list-service-map.yml
DOCPLANE_COVERAGE_GAP_BATCH_LIMIT=10
```

Never print the bearer. Use the routed DocPlane origin, not the direct API
port.

The bearer is resolved through the SECRETS-V3 `_FILE` contract
(`scripts/secret_source.py`), the same as the work catalogue, and **production delivers it by
file** as of 2026-09-23: `DOCPLANE_METER_LIST_TOKEN_FILE=/etc/charliehub/docplane-meter-list.token`
(`0600 root:root`), with no plaintext bearer left in the env file. Once `_FILE` is set an
unreadable or empty file is a hard failure and never falls back to a legacy value.

!!! note "What the file contract does and does not buy here"
    A correction to an earlier version of this page, which claimed an `EnvironmentFile` bearer
    is visible in `systemctl show --property=Environment`. **It is not** — systemd exposes only
    the file path (`EnvironmentFiles=`), never its contents; verified on hub2. The plaintext
    value was readable in `/proc/<pid>/environ` of the running unit, which on this host is
    root-only, the same audience as the `0600` env file itself. So the migration is not a
    material change in exposure on a single-admin host. What it does buy is a journal free of
    the once-a-minute `SecretSourceDeprecation` (88/hour before the change), and survival when
    the deprecated fallback is eventually removed — at which point both generators would fail
    closed instead.

Migrating a consumer is two reversible steps, in this order, because the contract says the file
wins when both are set:

1. write the bare-token file `0600 root:root`, add `<NAME>_FILE` to the env file, leave
   `<NAME>` in place, and prove a real run is clean (no deprecation warning, unit `Result=success`);
2. only then delete `<NAME>` from the env file, keeping a `0600` backup, and prove a run again.

!!! warning "A revoked bearer fails as a permissions error"
    A token the API has revoked returns `403 AUTH_PRINCIPAL_INACTIVE` — "principal or token
    is inactive". That reads like a scope problem and is not: stop, and check which credential
    the caller actually loaded before touching anything else.

## Service wiring

Adding a `service` label in the rule repository is the canonical fix. The
next import incrementally reconciles its `WATCHES` link; it does not rebuild
the graph.

For a repository that cannot yet be edited, curate
`config/meter-list-service-map.yml`. Patterns are case-sensitive shell globs
against rule names and apply only when the rule has no upstream service
label. The named service entity must already exist. Overlay links carry
`metadata.source=overlay`; upstream label links carry
`metadata.source=rule_label`. Zero matches and unknown services are loud
warnings. Conflicting matches refuse the run.

Generate a names-only curation aid without writes:

```bash
python3 scripts/meter_list.py --suggest-services \
  > meter-list-service-map-suggestions.json
```

Suggestions are evidence, never mappings. Review them with operational
owners before adding an overlay entry.

## Manual and scheduled reconciliation

Dry-run first:

```bash
export METER_RULES_DIR=/srv/monitoring/prometheus/rules
export METER_SOURCE_KEY=hub2.prometheus
python3 scripts/meter_list.py --dry-run
python3 scripts/meter_list.py
```

`METER_SOURCE_KEY` is required even for `--dry-run`: it controls generated page
paths and is the stable monitoring-source entity identity. Production uses the
existing `hub2.prometheus` SERVICE entity and generated meter-list namespace.
The scheduled wrapper passes flags through but supplies no environment; its
timer/config source must provide the same required values. Manual invocations
must export them explicitly as above, plus the routed API and named automation
bearer from **Required environment** before the non-dry run.

The schedulable entrypoint is a lock-protected wrapper around that exact
importer, not a second code path:

```bash
scripts/run_meter_list_reconciliation.sh
```

### The timer

Production installs `config/systemd/docplane-meter-list.{service,timer}`, the same shape as
the work catalogue's. The timer reconciles five minutes after boot and fifteen minutes after
each completed run, with a small randomized delay. Fifteen minutes rather than the work
catalogue's one: rule files change at deploy cadence, not continuously, and every tick costs
a full parse of the rule set plus API reads. An unchanged rule set takes the `UNCHANGED`
fingerprint fast path, so a quiet estate stays cheap.

Install from the pull-only deployment checkout, with the named automation environment already
in place at `/etc/charliehub/docplane-meter-list.env`:

```bash
install -o root -g root -m 0644 config/systemd/docplane-meter-list.service /etc/systemd/system/
install -o root -g root -m 0644 config/systemd/docplane-meter-list.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now docplane-meter-list.timer
systemctl start docplane-meter-list.service
```

The unit reads `/opt/charliehub/monitoring/prometheus/rules` read-only: the rule files are the
authoritative source and this generator is never their writer.

### What the schedule is worth, and how you know it ran

Unscheduled, the generated pages lag the deployed rules by however long it takes someone to
remember. On 2026-09-22 a rule file shipped at 20:55 UTC and its meter-list page did not
exist until the importer was run by hand two hours later; the credential had also been
revoked a month earlier, and nothing said so, because nothing was running.

Each run publishes the shared projection metrics through the node_exporter textfile
collector (`docplane_meter_list.prom`), so the loop's own liveness is observable:

| Series (label `artifact="meter-list-<source-slug>"`) | Meaning |
|---|---|
| `docplane_generated_projection_drift` | the published pages no longer match the rule files |
| `docplane_generated_projection_reconcile_success` | the last governed run succeeded |
| `docplane_generated_projection_last_run_unixtime` | when the last status check completed |

The drift metric is published **even when reconciliation fails**, and the alerts in
`monitoring/prometheus/rules/docplane-generated-projection-alerts.yml` (charliehub-hub2)
include an `absent()` guard: a timer that never runs is a firing alert, not silence.

A production invocation runs under the root execution identity so it can read
the protected meter-list environment. The wrapper's lock defaults to
`/run/lock/docplane-meter-list.lock`, the deployed implementation of the
logical meter-list exclusion domain. It is deliberately outside sticky
`/tmp`, so its ownership cannot depend on which account first created an old
lock inode. Override `DOCPLANE_METER_LIST_LOCK_FILE` only when every legitimate
caller uses the same replacement path. The wrapper opens the lock
nonblockingly; contention prevents the importer from starting and preserves
flock's existing conflict exit status.

A systemd timer may invoke that command with environment supplied by the
fabric secret/config source. An unchanged run prints `UNCHANGED`, replays the
existing fingerprint-bound nominal observation, and makes zero Model, Know or
Work changes. It neither creates work nor sends a notification.

The full coverage count remains at `GET /api/v1/observe/coverage`. A changed
import automatically advances the bounded Work projection. To triage the
next batch without source drift:

```bash
curl -fsS -X POST \
  "$DOCPLANE_API/api/v1/observe/coverage/reconcile-work?batch_limit=10" \
  -H "Authorization: Bearer $DOCPLANE_METER_LIST_TOKEN" \
  -H "Idempotency-Key: meter-list-gap-reconcile-$(date -u +%Y%m%d)"
```

List projected items with
`GET /api/v1/observe/coverage/work-items?status=OPEN`.

## Sprint 8 production rerun

Migration 009 is required because prior storage had neither reopenable Work
gap identity nor model-link metadata. Apply it through normal docs-api
startup and verify the ledger is contiguous 000–009.

The first 1.3.0 run on the Sprint 6 corpus should:

- retain the rule-source fingerprint
  `b9b1f94d0557039ff2761d114b31b92d53f99687881cf099c39a025b15cc8834`;
- update 256 existing rule attributes with their generated
  `source_page_path`;
- preserve the 92 upstream `WATCHES` edges while stamping their additive
  `metadata.source=rule_label` provenance;
- replace the 36 generated catalogue pages and publish a successor
  declaration because the generator contract moved (the permanent presence
  page remains authored and is not an artifact target);
- create at most ten new coverage Work items by default;
- create no overlay links until an operator curates the empty shipped map.

Exact production commands:

```bash
export METER_RULES_DIR=/srv/monitoring/prometheus/rules
export METER_SOURCE_KEY=hub2.prometheus
python3 scripts/meter_list.py --dry-run
python3 scripts/meter_list.py --suggest-services \
  > /tmp/meter-list-service-map-suggestions.json
python3 scripts/meter_list.py
python3 scripts/meter_list.py
```

The closing run must print literal `UNCHANGED`; its fingerprint and corpus
counts must match the preceding run, and it must make no Work-domain writes.
