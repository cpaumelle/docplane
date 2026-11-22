# DocPlane agent interface

DocPlane is endpoint-first. An agent uses a named bearer token and the same
contract as a human: the same API, the same revision checks and the same audit
trail. The step-by-step cold start is in [Agent onboarding](agent-onboarding.md).

## Discover

- `GET /.well-known/docplane.json` describes the deployment, including how to
  obtain a credential, without returning any credential.
- `GET /api/v1/operation-contracts` gives every change operation's payload schema
  and a full request example.
- `GET /openapi.json` gives the whole HTTP surface.

## Know: change a page

```text
SEARCH → READ EXACT CONTEXT → CREATE CHANGE → VALIDATE → PUBLISH → OBSERVE
```

- Stable page resource IDs survive path and navigation moves.
- Reads may return summary, outline, section, full source or edit context.
- Existing-page mutations carry the exact page revision. Section operations also
  carry the exact section hash.
- Validation evaluates the complete candidate corpus, navigation and redirects.
- A contributor may publish a valid change directly. Review comments are optional
  audit events.
- Publication snapshots prior versions, commits authored state atomically, builds
  the generated release and records certification evidence.
- Stale revisions, invalid navigation and deployment failures return structured
  errors or receipts. They never silently overwrite newer work.

## Work: coordinate with other agents

```text
READ BRIEF → DO THE WORK → RECORD A NAMED UPDATE (bound to the brief)
```

- Read the initiative's [brief](WORK_BRIEF.md) before working. Record outcomes
  with `POST /api/v1/initiatives/{id}/updates`, naming items and citing evidence.
  A stale brief fails with `WORK_CONTEXT_STALE`; read again and rebase.
- Park an out-of-scope idea with one capture call (`POST /api/v1/work/captures`
  or MCP `work_capture`) and return to the task. Triage is a separate act.
- Discover initiatives and read history and captures through the
  [safe WORK reads](WORK_CAPTURE_READS.md).
- Participants in the optional [addressed inbox](work-inbox.md) exchange
  messages scoped to one initiative.

Reports and messages are evidence, not authority. Nothing an agent records can
approve a production action, release a hold without a decision, or mark work
complete by itself.

## Identity

Every request is made as a named principal (`HUMAN`, `AGENT` or `AUTOMATION`).
Most principals are **contributors** with the same rights. Two narrower modes
exist, each fail-closed:

- `SCOPED_AUTOMATION`: a generator or observer limited to the artifacts,
  operations and page-path prefixes it was granted;
- `WORK_COLLABORATOR`: an inbox participant limited to its granted initiatives
  and refused on every contributor endpoint.

See [Authentication profiles](authentication-profiles.md) for how credentials
are obtained.

## Clients

MCP and HTTP are clients of the same API; HTTP is the complete contract.

- The bundled MCP server (`mcp/`) exposes the know tools and the `work_*` capture,
  triage, note, transition, link and disposition tools.
- The read-only [`docplane-mcp-shim`](../../docplane-mcp-shim/README.md) exposes
  page reads plus `work_brief` and `work_history`, and nothing that writes.

SSH, container shells, direct SQL and edits to generated files are break-glass
diagnostics only.
