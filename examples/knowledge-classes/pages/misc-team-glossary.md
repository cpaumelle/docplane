# Team glossary

Words this corpus uses with a precise local meaning.

**Certified release** — a generated site build whose publication receipt
and certification state agree; the only build the front serves.

**Contributor** — any approved active identity. There are no reader,
editor or reviewer tiers; workspaces classify content, not rights.

**Ledger** — an append-only record. Migration ledger: the ordered,
gapless list of applied schema migrations. Audit ledger: the event
stream every mutation writes into.

**Optimistic lock** — a write bound to the exact revision it read; a
concurrent change makes the write fail cleanly instead of overwriting.

**Publication receipt** — the durable record of one publication: what
was validated, what was built, what was promoted, by whom.

**Receipt replay** — re-issuing a request with the same idempotency key
and getting the original result instead of a second mutation.

**Single writer** — the one service allowed to mutate the database;
every other surface goes through it.
