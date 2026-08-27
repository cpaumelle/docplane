# Generated condition reconciliation

Generated-artifact conditions are a durable WORK projection of current
MODEL/OBSERVE/CATALOGUES authority. The supported attended command is:

```bash
python3 scripts/reconcile_generated_conditions.py \
  --family work \
  --idempotency-key 00000000-0000-4000-8000-000000000000
```

Replace the example UUID for each new logical reconciliation. Reuse it only
when retrying the exact same family reconciliation after an unknown client
outcome. The command never generates an invocation identity itself.

## Runtime identity

Condition reconciliation is separate custody from generation. Use a dedicated,
ACTIVE `AUTOMATION` principal (recommended display name:
`docplane-generated-condition-reconciler`) rather than a Work, Schema, or Meter
generator bearer. Its protected environment contract is:

```text
DOCPLANE_API=<canonical routed DocPlane endpoint>
DOCPLANE_GENERATED_CONDITIONS_TOKEN=<dedicated automation bearer>
DOCPLANE_GENERATED_CONDITIONS_PRINCIPAL_ID=<expected principal UUID>
```

The command authenticates with `/api/v1/me` and refuses to continue unless the
returned identity is that exact AUTOMATION principal. Credential issuance,
rotation, protected environment materialisation, and production execution are
separate operator gates.

## Authority and effects

The command accepts exactly one reviewed family: `work`, `schema`, or `meter`.
Each family resolves a fixed artifact/source identity and derives structured
CATALOGUES state from MODEL entities, page metadata, and existing semantic
links. It never infers relationships from rendered Markdown.

All reads use the DocPlane API. The only mutation available to the client is:

```text
PUT /api/v1/work/generated-artifacts/{artifact_id}/conditions
```

The request is the complete currently-true condition set produced by the pure
`generated_conditions.py` layer. The runner does not modify its briefings.
Unknown families, missing or ambiguous authority, truncated listings, source or
artifact identity conflicts, invalid page identity, and missing structured
MONITOR_RULE mappings all fail before the PUT.

The JSON output is allowlisted to artifact/family/invocation identity,
condition kinds/count, and the bounded reconciliation result. It does not echo
bearers, raw API error bodies, fingerprints, page content, or arbitrary receipt
fields.

There is no schedule, service, timer, generation action, observation action,
CATALOGUES mutation, execution-contract mutation, or remediation policy in this
command.
