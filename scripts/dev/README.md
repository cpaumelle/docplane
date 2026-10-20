# Isolated development instance

A persistent DocPlane instance for development and consumer testing, holding
**synthetic data only**. It is a separate Compose project (`docplane-dev`) built
from any committed checkout, with its own volumes, network and dev-only secrets.
Nothing is copied from another installation.

## Procedure

```bash
export DOCPLANE_SRC=<checkout to run>          # e.g. a task worktree at a PR head
D=<this repo>/scripts/dev/devstack.sh

$D init        # once: dev-only secrets in ~/.config/docplane-dev (0700/0600), DEV banner
$D start       # check isolation, build DOCPLANE_SRC at HEAD, start; volumes are kept
$D provision   # once per database: dev principals; tokens in ~/.config/docplane-dev/tokens (0600)
$D fixture     # once per database: synthetic WORK fixture (re-runs are no-ops)
$D status      # source SHA, health, bindings, capture counts

$D test        # full test suite against a DISPOSABLE PostgreSQL (never the instance)
DOCPLANE_TEST_ARGS="docs-api/tests/test_capture_reads_e2e.py -rA" $D test

$D compat origin/main   # older API on the project network as compat-api:8010
$D compat stop

DOCPLANE_DEV_RESET_CONFIRM=docplane-dev $D reset   # drop THIS project's volumes; rebuild + reload
```

To move the instance to another head, check that head out in `DOCPLANE_SRC`
and run `start` again. The database persists and migrations are applied by the
docs-api entrypoint as usual.

## Isolation

`check` runs before every `start` and fails unless the merged Compose model:

- binds every published port to `127.0.0.1` (API 28010, dashboard 28051, front
  28080, bundled MCP 28049). The overlay replaces port lists with `!override`;
  without that, Compose appends overlay ports and keeps the base wildcard bindings;
- publishes no PostgreSQL port;
- scopes all volumes and networks to the project, and sets no fixed container
  names;
- uses the `managed` access profile, no publication webhooks and a `DEV` site
  identity.

The scripts refuse to run on a host where a Compose project named `docplane`
is running, or whose name matches `DOCPLANE_DEV_REFUSE_HOSTS` (a shell pattern
you set for your production hosts). They also refuse project names that could
collide with a real installation (`docplane`, `docplane_*`).

## Synthetic fixture

`dev_fixture.py` is development tooling, not an operational path.

- **Supported APIs:** dev principals (bootstrap API), four initiatives with
  activities, 1,500 captures from three authors across every kind, and triage
  (about 450 attached, 300 discarded, 22 promoted) with notes.
- **Bodies, origins and notes:** ordinary text, structured origins, synthetic
  credential shapes (the redaction module's own synthetic patterns), and the
  canonical refusal cases: unsafe fenced secret, malformed marker, secret-named
  origin key.
- **Direct SQL, only via `devstack.sh psql`:** that command refuses any
  database without the `public.docplane_dev_instance_marker` row created by
  `start`. It is used only for historical shapes the API cannot create:
  backdated creation in blocks of 25 identical timestamps (60 tie groups),
  non-object legacy origins, and a pre-#171 activity body.

## Not provided

There is no routed hostname, DNS, TLS or fabric exposure. The instance is
loopback-only by design.
