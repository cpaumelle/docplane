# Isolated development instance

A persistent DocPlane instance for development and consumer testing. It is a
separate Compose project (`docplane-dev`) built from any committed checkout, with
its own volumes, network and dev-only secrets. Its database holds either the
synthetic fixture or, when refresh is configured, a scrubbed copy of a production
export (see [Refresh from a production export](#refresh-from-a-production-export)).
No production `.env`, key or token is ever copied.

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

## Refresh from a production export

`devstack.sh refresh` replaces the instance's database with the newest export a
deployment delivers into `DOCPLANE_DEV_REFRESH_INBOX`. Each export is a pair:
`docplane-<UTC>.dump` (`pg_dump -Fc --no-owner --no-privileges`) and a
`docplane-<UTC>.manifest` written last, which holds the dump checksum, source SHA,
dump time and the row counts taken in the dump's own snapshot. **Every refresh
overwrites local changes to the dev database.**

```bash
$D refresh status            # source SHA and dump time, last result, paused?, timer
$D refresh run               # apply the newest verified export now
$D refresh pause             # automatic (timer) runs do nothing until resume
$D refresh resume
```

What a run does, all inside `docs_staging`, which no dev service connects to:

1. Restores the dump.
2. Checks that the source migration ledger is a checksum-identical prefix of `DOCPLANE_SRC`.
3. Requires every foreign key to be validated, and the row counts to equal the manifest.
4. Deletes **all** source `api_tokens`, so no production credential can authenticate.
5. Replaces, with a marker, only those receipt, event and principal-metadata values that match the clear-token shape. All other history is kept, and the result is counted.
6. Carries the dev principals and their tokens by the explicit IDs in `~/.config/docplane-dev/dev-principals.ids`. It refuses if a source principal has one of those IDs.
7. Swaps by rename, health-checks, and swaps back automatically on failure.

Any failure leaves the working database in place. The first replacement keeps
the synthetic database as `docs_synthetic_<date>`; later ones keep the previous
copy as `docs_previous`. While paused, older exports are still pruned (the two
newest are kept). Refresh changes only data: the env file, overlay, bindings,
access profile and empty webhooks are untouched, and `check` still passes.
`fixture` refuses to run against a refreshed copy.

The hourly timer is `systemd/docplane-dev-refresh.{service,timer}`, run as `ubuntu`
with `~/.config/docplane-dev/refresh.env` holding `DOCPLANE_SRC`,
`DOCPLANE_DEV_REFRESH_INBOX` and `DOCPLANE_DEV_TOOLS`. `bash test_refresh.sh` tests
the whole path in a throwaway project: an incomplete transfer, a failed restore, a
failure after the swap, a successful scrub and carry, pause and lock, and an
identity collision.

## Not provided

There is no routed hostname, DNS, TLS or fabric exposure. The instance is
loopback-only by design.
