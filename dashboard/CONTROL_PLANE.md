# Dashboard authority

The dashboard is an HTTP client of the DocPlane API. It has no database, filesystem or release-store authority.

A named contributor token is kept in browser session storage and forwarded to the API. Human authoring follows the same contract as MCP and other clients:

`SEARCH → READ EXACT REVISION → EDIT → VALIDATE → PUBLISH`

Review is optional and never blocks publication. The API owns revision checks, atomic mutation, version history, deployment certification and rollback.

## Private-fabric authentication bootstrap

The private-fabric deployment advertises self-service contributor-token issuance at
`POST /api/v1/auth/self-issue`; it does not require an existing credential or human
approval. Tokens are named, contributor-scoped and time-bounded.

The dashboard must provide that bootstrap itself (or establish an equivalent authenticated
browser session). Requiring a user to obtain a token out of band and paste it into the
dashboard is not a complete sign-in flow. Until bootstrap succeeds, the dashboard must show
an explicit authentication state and action rather than an apparently empty operational
view.

The overview is authenticated by design because it includes contributor and change-control
data. A blank overview is therefore not evidence that the corpus is empty. Check `/healthz`
for unauthenticated corpus counts, then authenticate and load the overview.
