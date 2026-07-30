# Dashboard authority

The dashboard is an HTTP client of the DocPlane API. It has no database, filesystem or release-store authority.

A named contributor token is kept only in browser session storage and memory, then forwarded
to the API. Human authoring follows the same contract as MCP and other clients:

`SEARCH → READ EXACT REVISION → EDIT → VALIDATE → PUBLISH`

Review is optional and never blocks publication. The API owns revision checks, atomic mutation, version history, deployment certification and rollback.

## Private-fabric authentication bootstrap

The dashboard is a client of the existing DocPlane authentication contract; it does not own
an authentication or permission system. Discovery is authoritative for the active access
profile, acquisition mode, endpoint, method and operator procedure.

In `private_fabric`, routed fabric reachability is the admission boundary. The dashboard
automatically calls the credential-acquisition endpoint advertised by discovery, accepts the
server-generated short-lived `AGENT / CONTRIBUTOR` principal, validates it through ordinary
capabilities, and stores its clear bearer only in memory and `sessionStorage`. No name,
password, operator token handoff or approval is required. Cached bearers are validated before
reuse; a rejected bearer is cleared and replaced at most once through a single-flight
bootstrap.

In managed deployments, self-service is disabled. The dashboard does not attempt issuance;
it displays discovery's operator procedure and accepts the existing operator-issued bearer.
Manual bearer entry is a fallback, not the primary private-fabric flow.

The routed DocPlane origin is security-relevant: it supplies the trusted fabric-admission
context for self-issue. Direct dashboard or docs-api service ports are not equivalent and
must not inject or weaken that admission. If bootstrap fails outside the routed front, the
dashboard directs the user to the routed URL or the manual fallback.

The overview is authenticated by design because it includes contributor and change-control
data. A blank overview is therefore not evidence that the corpus is empty. Check `/healthz`
for unauthenticated corpus counts, then authenticate and load the overview.
