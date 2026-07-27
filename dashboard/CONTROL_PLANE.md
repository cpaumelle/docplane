# Dashboard authority

The dashboard is an HTTP client of the DocPlane API. It has no database, filesystem or release-store authority.

A named contributor token is kept in browser session storage and forwarded to the API. Human authoring follows the same contract as MCP and other clients:

`SEARCH → READ EXACT REVISION → EDIT → VALIDATE → PUBLISH`

Review is optional and never blocks publication. The API owns revision checks, atomic mutation, version history, deployment certification and rollback.
