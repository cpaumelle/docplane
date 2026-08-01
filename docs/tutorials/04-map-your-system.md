# 4 · Map your system

Prose explains; the **model** indexes. This tutorial builds the card index of what your system structurally *is* — so that "show me everything about that service" has one answer for humans and agents alike.

## Cards, wires, page links

Three primitives:

- **Entities** — one card per thing: `(entity_kind, entity_key)` is the stable handle, `attributes` carries kind-specific facts, `version` guards concurrent edits. Kinds are a closed, harvested vocabulary (`SYSTEM`, `SERVICE`, `NODE`, `VM`, `SITE`, `NETWORK`, `DATABASE`, `SCHEMA`, `API`, `ROUTE`, `DEVICE_MODEL`, `INTERFACE`, `ARTIFACT`, `MONITOR_RULE`); `GET /api/v1/model/contracts` publishes each kind's attribute checklist.
- **Entity links (wires)** — typed edges: `RUNS_ON`, `DEPENDS_ON`, `MEMBER_OF`, `WIRED_TO`, `EXPOSES`, `STORES_IN`, `GENERATED_FROM`, `WATCHES`.
- **Page links** — edges between cards and know pages: `DESCRIBES` (this page explains this entity), `OPERATES` (this runbook operates it), `DECIDES`, `CATALOGUES`.

The rule of thumb: **facts with one true value live on the card; understanding lives in pages wired to the card.** Depth stops at the schema — tables and columns belong in generated catalogues, not hand-made cards.

## Worked example: one host, one service, one database

Say your system is a small server running an app backed by PostgreSQL:

```bash
TOKEN='dp_...'; API='http://localhost:8080'
post() { curl -fsS -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: $1" "$API$2" -d "$3"; }

# The host
NODE=$(post node-1 /api/v1/model/entities \
  '{"entity_kind":"NODE","entity_key":"homeserver","display_name":"Home server",
    "attributes":{"os":"debian-12","role":"docker host"}}')
NODE_ID=$(printf '%s' "$NODE" | jq -r .entity_id)

# The service
SVC=$(post svc-1 /api/v1/model/entities \
  '{"entity_kind":"SERVICE","entity_key":"myapp","display_name":"My App",
    "attributes":{"runtime":"docker","port":8000}}')
SVC_ID=$(printf '%s' "$SVC" | jq -r .entity_id)

# The database
DB=$(post db-1 /api/v1/model/entities \
  '{"entity_kind":"DATABASE","entity_key":"myapp-postgres","display_name":"My App PostgreSQL",
    "attributes":{"engine":"postgres","major_version":16}}')
DB_ID=$(printf '%s' "$DB" | jq -r .entity_id)

# Wire them
post wire-1 "/api/v1/model/entities/$SVC_ID/links" "{\"relation\":\"RUNS_ON\",\"to_entity_id\":\"$NODE_ID\"}"
post wire-2 "/api/v1/model/entities/$SVC_ID/links" "{\"relation\":\"STORES_IN\",\"to_entity_id\":\"$DB_ID\"}"
```

Now link the service page you wrote in [Tutorial 3](03-author-your-first-pages.md):

```bash
PAGE_ID=$(curl -fsS -H "Authorization: Bearer $TOKEN" \
  "$API/api/v1/pages?path=services/myapp/index.md" | jq -r '.pages[0].resource_id')
post pagelink-1 "/api/v1/model/entities/$SVC_ID/page-links" \
  "{\"relation\":\"DESCRIBES\",\"page_resource_id\":\"$PAGE_ID\"}"
```

Open **model · Entities** in the dashboard: filter by kind, click `myapp`, and the entity page shows its attributes, both wires, the linked page, and (once [Tutorial 6](06-let-it-observe.md) runs) its live status. Every card also has a spoken address: `docplane://model/service/myapp`.

## Harvest, don't hand-copy

Hand-creating cards is right for the first dozen; after that, harvest from what your system already says about itself — a compose file, systemd units, DNS zones, proxy config. Two patterns:

- **Import scripts** run as a named `AUTOMATION` principal and reconcile cards idempotently (create-or-update by `(kind, key)`, version-guarded). The monitoring importer in Tutorial 6 is the shipped exemplar.
- **Generated catalogues** go further: a declared artifact (`POST /api/v1/model/artifacts`) binds generator, source entity and fingerprint, and republishes reference pages *only when the source changes* — behind a permanent, thin presence page. `GENERATED` pages can never be hand-edited while the declaration stands.

Attributes are secret-scanned and fail closed: a token-shaped value is rejected at the API. Facts, not credentials.

## When to add a card

A thing earns a card when you'd ever ask a question about it by name. A thing earns a *kind checklist* when several real instances share a shape. Until then, a loosely-validated card beats a speculative schema — the vocabulary is harvested, not invented.

Next: [Run your work →](05-run-your-work.md)
