# 2 · Install and first login

Fifteen minutes from clone to a connected dashboard.

## Prerequisites

Docker with the compose plugin, and `curl` + `jq` for the command-line moments. Nothing else — the containers build themselves.

## Install

```bash
git clone <this-repo> docplane && cd docplane
cp .env.example .env
```

Edit `.env` and set three secrets (long and random; they gate the database, event cursors and the MCP surface):

```dotenv
POSTGRES_PASSWORD=…
DOCPLANE_EVENT_CURSOR_SECRET=…
MCP_API_KEY=…
# Managed mode also needs the operator bootstrap secret:
DOCPLANE_BOOTSTRAP_TOKEN=…
```

Then bring up the core:

```bash
docker compose up --build -d postgres docs-api dashboard docs-web
```

The API container applies every migration in `db/migrations/` in order before serving — there is no separate database setup step. Verify:

```bash
curl -s http://localhost:8080/healthz
# {"status":"ok", … active/archived page counts …}
```

## Choose your access profile

DocPlane always authenticates with named bearer tokens; the **profile** decides how tokens are *obtained*:

- **`managed`** (the default) — an operator issues tokens. Right for anything public, external or shared.
- **`private_fabric`** — for deployments whose routed hostname is already inside a VPN / SD-WAN boundary. Anyone who can reach the front can self-issue a short-lived, individually named token; the dashboard does this automatically on load. Issuance is dual-gated (profile *and* the routed front's admission header), so exposing the raw API port never admits anyone.

Stay on `managed` for this tutorial. Read [Authentication profiles](../architecture/authentication-profiles.md) before ever switching — and never use `private_fabric` on a publicly reachable hostname.

## Issue your first token

```bash
set -a; . ./.env; set +a
bash ./scripts/bootstrap-contributor.sh "Your Name" HUMAN
# → JSON with the principal and its clear token — shown once, store it now
```

## First login and the tour

Open **http://localhost:8080/dashboard/** and connect with the token. The sidebar is the four-verb map:

- **Overview** — the campaign board: unclassified pages, review candidates, work inbox, coverage gaps, open changes. Every card is a deep link to where that work happens. On a fresh install everything reads zero — that's honest, not broken.
- **work · Queues & inbox** — capture box, triage inbox, initiatives with GTD states.
- **know · Explore** — browse the corpus directory by directory; select pages to stage moves.
- **know · Review** — the ranked attention queue plus staged reorganisation plans.
- **know · Classify** — the keyboard-driven classification workbench.
- **know · Author** — search, revision-bound editing, validate, publish, version history.
- **model · Entities** — the card index of your system.
- **observe · Coverage & evidence** — what's watched, what isn't, and the observations ledger.
- **Changes & versions** — every change with its receipts.

The footer shows who you are. A blank Overview before connecting is expected — it's authenticated by design because it includes contributor and change data; `/healthz` is the unauthenticated pulse.

## Optional: the rendered site and the example corpus

The generated documentation site lives at **http://localhost:8080/** — it's rebuilt and certified on every publication. Empty corpus, empty site.

Want something to look at before committing your own content? Seed the generic example corpus, which demonstrates the knowledge-class system end to end:

```bash
export DOCPLANE_API=http://localhost:8080
export DOCPLANE_TOKEN=dp_...        # the token you just issued
python3 examples/knowledge-classes/seed_examples.py
# full walkthrough: examples/knowledge-classes/README.md
```

Next: [Author your first pages →](03-author-your-first-pages.md)
