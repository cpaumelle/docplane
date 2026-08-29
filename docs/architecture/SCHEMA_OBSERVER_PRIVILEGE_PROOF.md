# Schema source-observer PostgreSQL privilege proof

Plan for the disposable-PostgreSQL experiment that resolves the one decision
recorded as **UNRESOLVED** in the ratified Schema source-observer design.

This document is a proof plan. It authorises no role creation, grant,
deployment, timer, execution-contract declaration, observation, generation,
publication or reconciliation — in production or anywhere else. Executing the
proof is a separate attended act against a disposable instance; acting on its
outcome is a further separate decision.

## 1. The unresolved decision

The ratified Schema observer design fixes everything except one thing: the
PostgreSQL identity the observer connects as. Already ratified and not in
question here — a dedicated Schema observer principal, `SCHEDULED` trigger, a
30-minute cadence, a 2-hour maximum evidence age, an unchanged generation owner
on `MANUAL`, the shared `schema-catalogue` exclusion domain, canary before
execution-contract declaration, units installed disabled and activated
separately, and a 24-hour observer soak.

Those cover the *DocPlane* identity — a named `AUTOMATION` principal with its
own bearer. They say nothing about the *database* identity. Today one
PostgreSQL role, configured as `CATALOGUE_SOURCE_USER`, is used by the
generator (`docs/operations/SCHEMA_CATALOGUE.md`). The question this proof
settles:

> Can the Schema source observer hold a PostgreSQL role distinct from the
> generator's, restricted to structural metadata, and still compute a
> fingerprint byte-identical to the generator's over the same source state?

## 2. Why it must be settled before the observer is built

The charter invariant is that source observation and generation are
independent operations, and that only a successful canonical probe fingerprint
participates in freshness comparison. Two failure modes make this a
correctness question rather than a hardening preference:

- **False drift.** If the observer's role sees less structure than the
  generator's, its fingerprint differs permanently. Correspondence reads
  `MISMATCH` forever, `DRIFTED` opens and never resolves, and the first
  actionable condition the remediation plane ever produces is an artefact of
  privilege rather than of reality.
- **Nominal independence.** If the observer must reuse the generator's role,
  the two operations are independent in process topology only, not in
  authority. That may still be an acceptable decision, but it must be a
  *declared* one, recorded in the execution contract as a known limitation —
  not inherited silently because nobody tested the alternative.

Building the observer first and discovering either at canary time costs the
principal, credential, wrapper, unit and canary that were built on the
assumption.

## 3. The reading surface

`introspect()` issues exactly four statements per table inside a read-only
transaction (`scripts/schema_catalogue.py`; after PR #173 the same functions
live in `scripts/schema_catalogue_source.py`, unchanged):

| Query | Reads | ACL-filtered? |
| --- | --- | --- |
| `_TABLES_SQL` | `pg_class`, `pg_namespace`, `obj_description` | hypothesis: no |
| `_COLUMNS_SQL` | `information_schema.columns` | hypothesis: **yes** |
| `_CONSTRAINTS_SQL` | `pg_constraint`, `pg_get_constraintdef` | hypothesis: no |
| `_INDEXES_SQL` | `pg_indexes` | hypothesis: no |

The asymmetry is the whole problem. Three of the four read `pg_catalog`
relations that PostgreSQL exposes to every role; one reads an
`information_schema` view whose definition filters on role and column
privilege. A role with no privileges is therefore expected to observe every
table, comment, constraint and index — and no columns at all. That is not a
loud failure. It is a structurally plausible projection with a stable, wrong
fingerprint, which is precisely the "reads clean while split" class this
fabric has been bitten by before.

Step 0 of the proof records the deployed view definition rather than trusting
this table:

```sql
SELECT pg_get_viewdef('information_schema.columns'::regclass, true);
```

## 4. Proof parameters

- **Disposable only.** A throwaway container, destroyed after the run. The
  proof never connects to the deployed database and creates no production role.
- **Genesis-applied.** `python docs-api/migrate.py apply --dir db/migrations`,
  so the fixture is the real catalogued surface (`docplane`, `docs`, `model`,
  `observe`, `work`), not a toy schema.
- **Major-version parity is mandatory.** ACL predicates in
  `information_schema` and the set of predefined roles vary by major version, so
  a proof on one major version is not a proof for another. The deployed source
  is `postgres:16-alpine` (`docker-compose.yml`), and `fresh-instance.yml`
  services `postgres:16-alpine`; the PR #173 seam test is recorded as having
  used a disposable PostgreSQL 17. **Resolve that discrepancy before running**,
  run the matrix on the deployed major version, and record `server_version` in
  the receipt.
- **Structure-only.** No row data is read, printed or retained by any arm.
- **Credentials are throwaway.** The disposable instance's passwords are
  generated per run, never templated in this document, never echoed, and never
  reused from any real environment.

## 5. Hypotheses

Each is falsifiable by one arm of the matrix.

- **H1 — catalogue symmetry.** A role holding no grant beyond the default
  `PUBLIC` `CONNECT` observes the same tables, comments, constraints and
  indexes as the owner role.
- **H2 — information_schema asymmetry.** That same role observes zero columns,
  producing a complete-looking structure with a divergent fingerprint.
- **H3 — `REFERENCES` sufficiency.** `USAGE` on each catalogued schema plus
  `REFERENCES` on its tables restores byte-identical structure and fingerprint
  *without* permitting any row to be read.
- **H4 — default-privilege durability.** Without `ALTER DEFAULT PRIVILEGES`,
  a table created after the grants reintroduces divergence; with it, parity
  survives new objects. Default privileges are per-granting-role, so they must
  be set for the role that applies migrations, not merely for a convenient one.
- **H5 — write refusal.** The observer role cannot `INSERT`, `UPDATE`,
  `DELETE` or `CREATE` in any catalogued schema, and
  `default_transaction_read_only` holds server-side rather than relying on the
  client's `SET TRANSACTION READ ONLY`.
- **H6 — `pg_catalog` fallback.** Replacing the `information_schema.columns`
  read with an equivalent `pg_attribute` read makes the projection
  privilege-independent, but changes the rendered type spellings and therefore
  the fingerprint — making it a projection-contract change, not a drop-in.

## 6. Matrix

| Arm | Role configuration | Measures |
| --- | --- | --- |
| A | owner / current `CATALOGUE_SOURCE_USER` | reference structure + fingerprint |
| B1 | new role, no explicit grants (default `PUBLIC` `CONNECT`) | H1, H2 |
| B2 | B1 + `USAGE` on catalogued schemas | whether schema `USAGE` alone matters |
| C1 | B2 + `REFERENCES` on all existing tables | H3 |
| C2 | C1 + `ALTER DEFAULT PRIVILEGES … GRANT REFERENCES` | H4 |
| D | B2 + `SELECT` on all tables | contrast: parity at the cost of row exposure |
| E | B1 with `pg_catalog`-only column introspection | H6 |

Arm D is measured, not proposed. It is expected to achieve parity by granting
the observer read access to every row in the corpus database, which is a
larger exposure than the observer needs and is rejected unless C1/C2 fail and
E is judged too costly.

## 7. Procedure

Every arm follows the same shape: connect as the arm's role, run the
*unmodified* `introspect()` and `fingerprint()` over the catalogued schema
list, and compare against arm A.

```bash
# 1. disposable instance on the deployed major version, throwaway credentials.
#    The password lives in an unechoed shell variable and dies with the shell.
proof_password="$(openssl rand -hex 24)"
docker run --rm -d --name docplane-privilege-proof \
  -e POSTGRES_DB=docs -e POSTGRES_USER=docs \
  -e POSTGRES_PASSWORD="$proof_password" \
  -p 55432:5432 postgres:16-alpine

# 2. genesis, through the repository's own runner
DB_HOST=127.0.0.1 DB_PORT=55432 DB_NAME=docs DB_USER=docs DB_PASS="$proof_password" \
  python docs-api/migrate.py apply --dir db/migrations

# 3. step 0 evidence: what this major version actually filters on
PGPASSWORD="$proof_password" psql -h 127.0.0.1 -p 55432 -U docs -d docs \
  -c "SELECT version()" \
  -c "SELECT pg_get_viewdef('information_schema.columns'::regclass, true)"
```

Arm roles are created inside the disposable instance only:

```sql
-- B1
CREATE ROLE observer LOGIN PASSWORD :'throwaway';
ALTER ROLE observer SET default_transaction_read_only = on;

-- B2
GRANT USAGE ON SCHEMA docplane, docs, model, observe, work TO observer;

-- C1
GRANT REFERENCES ON ALL TABLES IN SCHEMA docplane, docs, model, observe, work
  TO observer;

-- C2 (as the role that applies migrations)
ALTER DEFAULT PRIVILEGES FOR ROLE docs
  IN SCHEMA docplane, docs, model, observe, work
  GRANT REFERENCES ON TABLES TO observer;
```

Comparison is made on the structure and its digest, never on printed digests:

```python
import psycopg2
from schema_catalogue import introspect, fingerprint   # schema_catalogue_source after #173

schemas = ["docplane", "docs", "model", "observe", "work"]
with psycopg2.connect(owner_dsn) as owner, psycopg2.connect(observer_dsn) as obs:
    reference = introspect(owner, schemas)
    candidate = introspect(obs, schemas)

equal_structure = reference == candidate
equal_digest = fingerprint(reference) == fingerprint(candidate)
```

H4 is measured by creating one table as the migrating role *after* the grants
and repeating the comparison. H5 is measured by attempting one write per
catalogued schema as the observer role and requiring every attempt to be
refused.

## 8. Pass criteria

An arm passes only if all of the following hold:

1. `introspect()` under the arm's role is **structurally equal** to arm A's,
   and its fingerprint is byte-identical;
2. every table in the catalogued schemas carries a non-empty column list — an
   equal-but-empty projection is a failure, not a pass;
3. no row of any catalogued table is readable by the arm's role;
4. every write attempt is refused;
5. parity survives a table created after the grants (H4 arms);
6. the comparison is reproduced on a second, freshly created disposable
   instance, so a pass is not an artefact of one container's state.

## 9. Decision rules

The proof produces exactly one of three outcomes, and each authorises a
different next step — none of which this document grants.

- **C1/C2 pass.** The observer gets its own least-privilege PostgreSQL role;
  independence is structural. The next authorised step is the observer
  implementation slice, with the role and its default privileges named in the
  execution contract, and `REFERENCES`-only grants documented in
  `docs/operations/SCHEMA_CATALOGUE.md` as a standing requirement of every
  future migration.
- **C1/C2 fail, E passes.** Privilege independence requires changing what the
  generator reads. That is a projection-contract change: it moves the
  fingerprint, needs a generator version and contract-version decision, a
  re-baseline of the artifact's last generation fingerprint, and a successor
  handoff. It must be decided and ratified **before** any observer is built,
  not discovered during a canary.
- **Both fail.** The observer shares the generator's PostgreSQL role. That is
  a permissible outcome, but the execution contract must then record
  explicitly that database-level independence is not achieved, so that
  "independent source observation" is never read as stronger evidence than it
  is.

If C1 passes but C2 fails, treat it as a failure of the whole approach: a
parity that silently lapses the next time a migration adds a table is a false
clean waiting for a release.

## 10. Receipt

The proof records a bounded receipt, and records verdicts rather than digests
— raw source fingerprints do not belong in remediation evidence:

```text
server_version, image tag, genesis migration count
arm, structure_equal (bool), digest_equal (bool), tables, columns_observed
row_read_refused (bool), write_refused (bool)
post-grant new-table parity (bool, H4 arms)
second-instance reproduction (bool)
outcome -> decision rule selected
```

No DSN, password, role password, digest value or source row appears in the
receipt, the journal or the pull request.

## 11. What this proof deliberately does not settle

- Whether the Schema observer is authorised to be built, deployed, enabled or
  scheduled.
- The DocPlane `AUTOMATION` principal, its bearer, or its rotation — already
  ratified and separate from the database role.
- Whether `EXECUTION_CONTRACT_MISSING` or `SOURCE_OBSERVATION_MISSING` may be
  resolved. Both remain open until the authorised acts that resolve them occur.
- Anything about the Meter or Work families. Their soaks and gates are
  independent and this proof must not touch them.

## 12. PR review disclosures (charter §14)

1. **Lane and layer.** Evidence lane, source-observation layer — design only.
2. **Authoritative inputs.** The ratified Schema observer decisions, the
   deployed generator's introspection surface, and the deployed PostgreSQL
   major version.
3. **Durable state it may mutate.** None. This is a document.
4. **What it does not mutate.** Generation, publication, ownership,
   `CATALOGUES`, `OBSERVE`, artifact state, WORK conditions, credentials,
   runtime units, and the production database.
5. **Idempotency and failure behaviour.** Not applicable; the described
   procedure is repeatable by construction and every arm is destroyed with its
   container.
6. **Ordering.** Precedes the Schema observer implementation slice, which
   precedes the observer canary, which precedes execution-contract declaration.
7. **Downstream contract tested.** None yet — that is the point of the
   experiment this plan describes.
8. **Authorised.** Merge only. Not deployment, scheduling, credential
   mutation, production execution or publication.
