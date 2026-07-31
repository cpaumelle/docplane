# Restore drill report — Q2

A record of something that happened. This page is evidence: it is never
updated to say what *should* have happened, only superseded by the next
drill's report.

## What was exercised

Full restore of the documentation database from the previous night's
backup into a scratch environment, followed by a publication retry and
certification check, using only the published runbook.

## Timeline

| Step                          | Duration | Outcome |
|-------------------------------|----------|---------|
| Provision scratch environment | 4 min    | ok      |
| Restore database dump         | 2 min    | ok      |
| Start docs-api (migrations)   | 1 min    | ok — ledger contiguous |
| Publication retry             | 3 min    | ok — certification CURRENT |
| Spot-check 10 random pages    | 6 min    | ok — content matched source |

Total: 16 minutes from decision to certified site.

## Findings

1. The runbook's restore section was followed as written; no undocumented
   steps were needed. That is the pass condition and it held.
2. One surprise: the scratch environment's clock skewed 40 s, which made
   audit timestamps look out of order until NTP settled. Noted for the
   runbook's preconditions.

## Disposition

Drill passed. Finding 2 raised as a runbook amendment; next drill after
the next migration lands.
