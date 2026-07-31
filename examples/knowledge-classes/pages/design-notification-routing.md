# Notification routing

A proposal under exploration. Nothing on this page is decided; when a
direction is chosen it becomes an ADR and this page links to it.

## Problem

Publication failures and certification changes are only visible to an
operator who goes looking. We want them pushed to wherever the team
already lives, without inventing a notification subsystem.

## Options considered so far

**Webhook fan-out.** docs-api POSTs deployment and certification events
to configured URLs. Simple, composable with chat bridges the team
already runs. Open question: retry semantics when a receiver is down.

**Digest page.** A generated page summarising the last N deployment
attempts, refreshed on every publication. Zero new infrastructure, but
it is pull, not push — it solves a different problem.

**Both.** The digest page is cheap and useful regardless; webhooks are
the actual answer to "tell me when it breaks".

## Current leaning

Digest page first (it is a generated artifact, a pattern we already
have), webhooks as a follow-up with an explicit delivery contract.

## Not yet resolved

- Webhook authentication story (signed payloads? bearer per receiver?)
- Whether certification-state *recovery* notifies, or only failure
