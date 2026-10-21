# One documented run procedure (manual, repeatable)

The agent launch stays manual. This tool pins, reads, checks and renders; it files nothing, schedules nothing and holds no index. Everything below is read-only
against WORK. Fixtures in the tests are synthetic; this package contains no production content, credential or case-specific answer.

## 0. Authorised read configuration
* Read access to a governed consolidated MCP (or a frozen disposable copy of DocPlane with its own MCP) under an identity you are authorised to use. Never borrow another
  principal's identity or reuse a production credential against a copy.
* The credential is a 0600 key file passed by path (`CHARLIEHUB_MCP_API_KEY=` line, parsed, never sourced, never in argv, a URL, the config or the repository).
* Verify the instance's identity before the run (for a frozen copy: the pinned dump hash and source commit) and record what you verified in `instance.expected_identity`; verify it
  again after the run and keep both records. Any difference invalidates the run.
* **KNOW access.** KNOW verbs are available only when the config says `know_access: SEARCHED`, which needs a docs credential for the same instance. Otherwise set
  `NOT_SEARCHED_NO_CREDENTIAL` (or `NOT_SEARCHED_OTHER`): the read tool refuses KNOW reads, every intake entry must say so, and the briefing must list KNOW as omitted. It limits
  conclusions: nothing may be said to be absent from KNOW; any input whose home or evidence could be a KNOW page stays uncertain or provisional; WORK-only coverage must be stated as such.

## 1. Pin the run (once)
Write `spec.json` (all keys required):
```
{"scope_id": "run-1", "snapshot_cutoff": "2026-01-01T00:00:00Z",
 "instance": {"label": "frozen copy", "url": "http://127.0.0.1:PORT/mcp", "expected_identity": {"dump_sha256": "...", "source_commit": "..."}},
 "know_access": "NOT_SEARCHED_NO_CREDENTIAL", "incoming_record_ids": ["<uuid>", ...], "initiative_ids": ["<uuid>", ...],
 "pinned_files": [{"label": "register", "path": "register-at-commit.md"}], "prompt_files": ["rules.md", "task.md"],
 "model_configuration": "agent type, model, effort", "read_budget": 250,
 "authoritative_registers": [{"register_id": "programme-register", "description": "...", "pinned_file_label": "register"}]}
```
`python3 -m work_worker config create spec.json --out run/config.json` writes a read-only config with one snapshot cutoff, the hashes of the pinned register, the prompt bundle and the
contract code (schema, validator, ledger, evaluations). `config verify run/config.json` detects any later edit or changed file. A conflicting config is rejected everywhere below.

## 2. Launch the interpreting agent (manual, fresh context)
Give it the pinned prompt files and tell it to read only through
`python3 -m work_worker read --config run/config.json --log run/read-log.jsonl --key-file <key file> <verb> '<json>'`
(verbs `docplane_work_get`, `docplane_work_capture_get`, `docplane_work_captures`, `docplane_work_initiatives`; `REGISTER '{"label":"register","grep":"..."}'` for the pinned register; `MARK`).
The tool removes records created after the cutoff, logs every call, issues fingerprints, refuses write verbs and (without KNOW access) docs verbs, and stops at the read budget. The agent writes its outputs once.

## 3. Check
`python3 -m work_worker check --config run/config.json --interpretation interpretation.json --log run/read-log.jsonl [--evaluations evaluations.json] [--previous prev.json --registry registry.json] --report check.json`
Exit status 1 rejects the run: contract violations, a header that conflicts with the config, a log written under another config, a record cited without being read in this run (fingerprint),
a search scope the log does not show, or lost/duplicated obligations. Do not edit the outputs to make them pass: record the rejection with the output and, if needed, do a new, labelled run.

## 4. Mint ids
`python3 -m work_worker mint --interpretation interpretation.json [--registry-in registry.json] --out registry-new.json`. Ids are issued once per (record, local id) and refuse to change.

## 5. Render
`python3 -m work_worker render --config run/config.json --interpretation interpretation.json --run-label r1 [--ledger-in ledger.json] [--applied applied.json] [--ops-log ops.jsonl] --out rendered/`
The directory must be new. It receives `briefing.md` (at most 400 words; the incomplete-scope and KNOW banner first; canonical facts apart from proposed organisation),
`appendix.md` (everything, linked from the briefing) and `ledger.json`. The ledger keeps every proposal from every run; absence from a later run only flags it, and rendering fails if any pending proposal would
not appear in the appendix. The display cap limits the briefing only.

## 6. Hand off
Checksum all files (`sha256 …`) and make them read-only. Originals are never edited; a correction is a new directory labelled as regression work, and a rerun is a new run with a new config. Output directories
are refused if they exist non-empty or contain an input.

## What this does not do
No triage, attach, promote, discard, note or capture; no scheduling; no index; no body search; no KNOW access without a credential. Initiative state shown is the instance's current state: revisions cannot be dated.
