# Authentication profiles

DocPlane always uses named bearer principals for protected API reads and writes. The deployment profile controls **how a caller is admitted to obtain that bearer credential**; it does not remove bearer authentication, exact-revision concurrency, publication receipts, expiry, revocation, or audit history.

## Profiles

### `managed` — safe default

Use `DOCPLANE_ACCESS_PROFILE=managed` for public, Internet-reachable, partner-facing, or otherwise externally exposed installations.

- Self-service issuance is disabled.
- `POST /api/v1/auth/self-issue` returns 404.
- An operator issues named HUMAN, AGENT, or AUTOMATION credentials through the bootstrap administration path.
- The bootstrap credential remains operator-only and is never exposed through discovery.
- `/.well-known/docplane.json` reports `self_service: false` and the operator-issued procedure.

This is the repository default. An adopter must make an explicit deployment decision before enabling network-position admission.

### `private_fabric` — fabric reachability is admission

Use `DOCPLANE_ACCESS_PROFILE=private_fabric` only when the routed DocPlane hostname is protected by an internal network boundary such as a VPN, private SD-WAN, restricted reverse-proxy middleware, or an equivalent trusted fabric.

A cold agent can discover the contract, call `POST /api/v1/auth/self-issue` through the routed front, receive a short-lived named AGENT contributor token, and use the normal bearer-authenticated API. No host access, bootstrap secret, helper script, sudo, or human approval is required.

Issued authority is constrained by code: AGENT only, CONTRIBUTOR only, mandatory expiry no longer than 24 hours, token hash storage only, audited source evidence, and bounded per-source/global issuance rates. The caller cannot request HUMAN, AUTOMATION, administrator, or permanent authority.

### Self-issue rate-limit contract

The trusted front supplies the observed source address and docs-api stores only
its SHA-256 fingerprint. Limits are isolated by that fingerprint; activity from
one routed source does not consume another source's allowance. The repository
defaults allow:

- a burst of 12 credentials per observed source in 60 seconds;
- a sustained 30 credentials per observed source in 3,600 seconds;
- 300 credentials globally in 3,600 seconds.

All windows are rolling windows, not wall-clock buckets. A rejection is HTTP
429 with `Retry-After` set to the seconds until the oldest issuance in the
limiting window expires. The structured `detail` includes
`SELF_ISSUE_RATE_LIMITED`, `scope`, `limit`, `window_seconds` and
`retry_after_seconds`. `X-RateLimit-Limit` and `X-RateLimit-Remaining: 0` are
also returned.

Successful issuance is logged as `event=self_issue_issued`; throttling is logged
as `event=self_issue_rate_limited` with scope, counts, window and a truncated
source fingerprint. Neither log contains a clear bearer or token hash.

## Trust boundary

`private_fabric` deliberately treats reachability of the routed internal hostname as the admission decision. This is the same high-level boundary used by the legacy internal Docs API it replaced, upgraded from a shared retrievable key to short-lived, individually attributable capabilities.

Admission is dual-gated:

1. docs-api must have `DOCPLANE_ACCESS_PROFILE=private_fabric`;
2. the request must traverse the trusted front's exact self-issue route, which overwrites source metadata and injects `X-DocPlane-Fabric-Admission: 1`.

Neither gate is sufficient alone. Direct access to docs-api cannot self-issue, even in private-fabric mode. Caller-supplied internal admission headers are cleared on other API routes.

Keep the direct docs-api port loopback-only or otherwise unreachable from untrusted networks. Enabling `private_fabric` on a publicly reachable routed hostname grants any reachable client the ability to mint a short-lived contributor credential.

## Configuration

```dotenv
# Safe default
DOCPLANE_ACCESS_PROFILE=managed

# Internal-only deployment
# DOCPLANE_ACCESS_PROFILE=private_fabric
# DOCPLANE_SELF_ISSUE_TTL_SECONDS=86400
# DOCPLANE_SELF_ISSUE_SOURCE_BURST_LIMIT=12
# DOCPLANE_SELF_ISSUE_SOURCE_BURST_WINDOW_SECONDS=60
# DOCPLANE_SELF_ISSUE_SOURCE_LIMIT_PER_HOUR=30
# DOCPLANE_SELF_ISSUE_GLOBAL_LIMIT_PER_HOUR=300
```

The TTL is bounded between 300 and 86400 seconds. Rate-limit settings are
bounded in code. Discovery publishes the effective values so clients do not
need to infer deployment-local tuning.

## Machine-readable behavior

`/.well-known/docplane.json` is authoritative. It reports the active profile, whether self-service is enabled, the exact endpoint and method, whether an existing credential is required, fixed principal kind and role, default/maximum TTL, and a complete request example. Agents should follow discovery rather than infer policy from a hostname.

## Non-goals

This is not cryptographic workload identity. A supplied display name is an audit label bound to observed network and user-agent evidence. Deployments needing stronger identity should use `managed` mode or add authenticated ingress, mTLS, workload identity, or an equivalent control.
