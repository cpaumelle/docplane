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
DOCPLANE_METER_LIST_TOKEN=<named AUTOMATION bearer>
METER_RULES_DIR=/srv/monitoring/prometheus/rules
METER_SOURCE_KEY=prometheus.main   # required; stable — it is entity identity
METER_SERVICE_MAP=/srv/docplane/config/meter-list-service-map.yml
DOCPLANE_COVERAGE_GAP_BATCH_LIMIT=10
```

Never print the bearer. Use the routed DocPlane origin, not the direct API
port.

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
python3 scripts/meter_list.py --dry-run
python3 scripts/meter_list.py
```

The schedulable entrypoint is a lock-protected wrapper around that exact
importer, not a second code path:

```bash
scripts/run_meter_list_reconciliation.sh
```

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
python3 scripts/meter_list.py --dry-run
python3 scripts/meter_list.py --suggest-services \
  > /tmp/meter-list-service-map-suggestions.json
python3 scripts/meter_list.py
python3 scripts/meter_list.py
```

The closing run must print literal `UNCHANGED`; its fingerprint and corpus
counts must match the preceding run, and it must make no Work-domain writes.
