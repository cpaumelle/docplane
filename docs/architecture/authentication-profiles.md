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

A cold agent can:

1. discover `/.well-known/docplane.json` without credentials;
2. call `POST /api/v1/auth/self-issue` through the routed front;
3. receive one short-lived named AGENT contributor token;
4. use the normal bearer-authenticated API.

No host access, bootstrap secret, helper script, sudo, or human approval is required.

The issued token is constrained by code:

- principal kind is always `AGENT`;
- role is always `CONTRIBUTOR`;
- expiry is mandatory and cannot exceed 24 hours;
- only the token hash is stored;
- source evidence and issuance metadata are audited;
- per-source and global hourly issuance limits apply;
- the caller cannot request HUMAN, AUTOMATION, administrator, or permanent authority.

## Trust boundary

`private_fabric` deliberately treats reachability of the routed internal hostname as the admission decision. This is the same high-level trust boundary used by the legacy CharlieHub Docs API, but replaces its shared retrievable API key with short-lived, individually attributable capabilities.

The trusted front is part of the security boundary:

- the exact self-issue route injects `X-DocPlane-Fabric-Admission: 1`;
- it overwrites the observed source address;
- caller-supplied copies of internal admission headers are discarded on other API routes;
- direct access to the docs-api service does not satisfy admission, even when `private_fabric` is configured.

Deployments must therefore keep the direct docs-api port loopback-only or otherwise unreachable from untrusted networks. Enabling `private_fabric` on a publicly reachable routed hostname grants any reachable client the ability to mint a short-lived contributor credential.

## Configuration

```dotenv
# Safe default for public/external installations
DOCPLANE_ACCESS_PROFILE=managed

# Internal-only installation
# DOCPLANE_ACCESS_PROFILE=private_fabric
# DOCPLANE_SELF_ISSUE_TTL_SECONDS=86400
# DOCPLANE_SELF_ISSUE_SOURCE_LIMIT_PER_HOUR=10
# DOCPLANE_SELF_ISSUE_GLOBAL_LIMIT_PER_HOUR=120
```

`DOCPLANE_SELF_ISSUE_TTL_SECONDS` is bounded between 300 and 86400 seconds. The rate-limit settings are also bounded in code.

## Machine-readable behavior

`/.well-known/docplane.json` is authoritative for clients. It reports:

- the active access profile;
- whether self-service is enabled;
- the exact endpoint and method;
- whether an existing credential is required;
- the fixed principal kind and role;
- default and maximum TTL;
- a complete request example.

Agents should follow discovery rather than assuming the profile from the hostname.

## Non-goals

This mechanism is not cryptographic workload identity. A supplied display name is an audit label bound to observed network and user-agent evidence. Deployments that need stronger identity should use `managed` mode or place an authenticated ingress, mTLS identity, workload identity, or equivalent control in front of DocPlane.
