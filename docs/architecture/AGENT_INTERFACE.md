# DocPlane agent interface

DocPlane is endpoint-first. An approved agent uses a named bearer token and the same contributor contract as a human.

## Normal workflow

```text
DISCOVER → SEARCH → READ EXACT CONTEXT → CREATE CHANGE → VALIDATE → PUBLISH → OBSERVE
```

- `GET /.well-known/docplane.json` describes the deployment without returning credentials.
- Stable page resource IDs survive path and navigation moves.
- Reads may return summary, outline, section, full source or edit context.
- Existing-page mutations carry the exact page revision and bounded section operations also carry the exact section hash.
- Validation evaluates the complete candidate corpus, navigation and redirect state.
- Any active contributor may publish a valid change directly; review comments are optional audit events.
- Publication snapshots prior versions, commits authored state atomically, builds the generated release and records certification evidence.
- Stale revisions, invalid navigation and deployment failures return structured errors or receipts; they never silently overwrite newer work.

MCP and HTTP are clients of the same API. SSH, container shells, direct SQL and generated-file edits are break-glass diagnostics only.
