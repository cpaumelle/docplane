#!/usr/bin/env python3
"""Dev-instance provisioning and the synthetic WORK fixture.

Development tooling only; never operational. Invoked by scripts/dev/devstack.sh.

provision  creates dev-only principals through the bootstrap API of the local dev
           instance and writes each clear token to <tokens>/<name>.token (0600).
           Tokens are never printed. Existing working tokens are kept.
load       builds the synthetic fixture (idempotent; marker-guarded):
           * API: initiatives, activities, ~1,500 captures from several authors,
             then triage (attach / promote / discard) with notes;
           * direct SQL, ONLY through `devstack.sh psql` (which refuses any
             database without the dev-instance marker): historical cases the API
             cannot create -- backdated and tied creation timestamps and
             non-object legacy origins.
Every credential-shaped string here is synthetic (the redaction module's own
synthetic shapes); none grants access anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FIXTURE = "dev-synthetic-v1"
NS = uuid.UUID("6b1f5c1e-6a43-4d0f-9b52-5d1c3a0d2f71")  # deterministic idempotency keys
PRINCIPALS = {
    # name: (kind, purpose)
    "dev-fixture-operator": ("HUMAN", "fixture operator; initiatives and triage"),
    "dev-producer-sweeper": ("AUTOMATION", "synthetic automated capture producer"),
    "dev-producer-agent": ("AGENT", "synthetic agent capture producer"),
    "dev-mcp-work-evidence": ("AUTOMATION", "dedicated WORK identity for the consolidated MCP adapter under test"),
}
KINDS = ["IDEA", "NEXT_ACTION", "FINDING", "QUESTION", "BUG", "IMPROVEMENT"]
TOTAL = 1500

# Synthetic secret shapes (migration/redaction.py DEFAULT_RULES).
AKIA = "AKIA" + "SYNTHETICDEV0001"
GHP = "ghp_" + "SyntheticDevFixtureToken0123456789ab"
BEARER = "Bearer " + "synthetic.dev.fixture.bearer.value"
HEX = "deadbeef" * 6


def key(*parts: object) -> str:
    return str(uuid.uuid5(NS, "/".join(map(str, parts))))


class Api:
    def __init__(self, base: str, token: str | None = None, bootstrap: str | None = None):
        self.base, self.token, self.bootstrap = base.rstrip("/"), token, bootstrap

    def call(self, method: str, path: str, body: dict | None = None, idem: str | None = None):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if self.bootstrap:
            headers["X-DocPlane-Bootstrap-Token"] = self.bootstrap
        if idem:
            headers["Idempotency-Key"] = idem
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read() or b"null")
            except ValueError:
                return exc.code, None


def env_value(env_file: Path, name: str) -> str:
    for line in env_file.read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1]
    raise SystemExit(f"{name} missing from {env_file}")


def read_token(tokens: Path, name: str) -> str:
    return (tokens / f"{name}.token").read_text().strip()


def provision(args) -> None:
    tokens = Path(args.tokens)
    tokens.mkdir(mode=0o700, parents=True, exist_ok=True)
    boot = Api(args.api, bootstrap=env_value(Path(args.env_file), "DOCPLANE_BOOTSTRAP_TOKEN"))
    for name, (kind, purpose) in PRINCIPALS.items():
        path = tokens / f"{name}.token"
        if path.exists():
            code, _ = Api(args.api, read_token(tokens, name)).call("GET", "/api/v1/work/captures?limit=1&view=metadata&status=all")
            if code in (200, 422):  # authenticated (422 = older server rejecting view)
                print(f"kept {name} ({kind})")
                continue
        code, resp = boot.call("POST", "/api/v1/bootstrap/principals", {
            "display_name": name, "principal_kind": kind, "expires_at": None,
            "metadata": {"issued_via": "scripts/dev/dev_fixture.py", "fixture": FIXTURE, "purpose": purpose},
        })
        if code != 201 or not isinstance(resp, dict) or "token" not in resp:
            raise SystemExit(f"provision {name}: HTTP {code}")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(resp["token"])
        print(f"issued {name} ({kind}) principal {resp['principal_id']}")


def sql(runner: str, statement: str) -> str:
    out = subprocess.run([runner, "psql"], input=statement, text=True, capture_output=True)
    if out.returncode != 0:
        raise SystemExit("direct fixture SQL refused or failed: " + out.stderr.strip()[-400:])
    return out.stdout.strip()


def body_for(i: int) -> tuple[str, dict]:
    """Deterministic synthetic body and origin for capture i."""
    rnd = random.Random(i)
    origin: dict = {"fixture": FIXTURE, "seq": i, "producer": rnd.choice(["sweeper", "agent", "operator"])}
    case = i % 20
    text = f"Synthetic capture {i}: {rnd.choice(['drift observed on', 'follow up on', 'question about', 'improve'])} subsystem-{i % 37}."
    if case == 1:
        text += f" Leaked key in a log line: {AKIA} (synthetic)."
    elif case == 2:
        text += f" Token pasted by mistake {GHP} in prose."
    elif case == 3:
        text += f" Header was Authorization: {BEARER} during the probe."
    elif case == 4:
        text += f" Long hex value {HEX} quoted from a dump."
    elif case == 5:
        # Canonical refusal: secret in an unsafe fenced position.
        text += f"\n\n```sh\necho key({AKIA})\n```\n"
    elif case == 6:
        # Canonical refusal: malformed redaction marker.
        text += " Prior redaction left {<REDACTED:PASSWORD:old>}} behind."
    elif case == 7:
        # Safe fenced replacement (assignment RHS).
        text += f"\n\n```sh\nexport ACCESS_KEY={AKIA}\n```\n"
    elif case == 8:
        origin.update({"cmd": f"probe --header {BEARER}", "host": "dev-node-1"})
    elif case == 9:
        origin.update({"note": f"see token({GHP})"})  # decoded string: redacted in prose position
    elif case == 11:
        origin.update({"password": "synthetic-dev-value"})  # secret-named key: whole origin withheld
    elif case == 12:
        origin.update({"note": "residue {<REDACTED:TOKEN:x>}}"})  # malformed marker: origin withheld
    elif case == 13:
        origin.update({"source": {"kind": "ci", "run": i, "ref": f"refs/pull/{i}/head"}})
    return text, origin


def load(args) -> None:
    tokens = Path(args.tokens)
    if sql(args.sql_runner, "SELECT count(*) FROM public.docplane_dev_instance_marker;") != "1":
        raise SystemExit("dev-instance marker missing")
    if sql(args.sql_runner, "SELECT to_regclass('public.docplane_dev_refresh') IS NOT NULL;") == "t":
        raise SystemExit("this database is a refreshed production copy; the synthetic fixture is for synthetic databases only")
    done = sql(args.sql_runner, f"SELECT count(*) FROM work.captures WHERE origin->>'fixture' = '{FIXTURE}' OR (jsonb_typeof(origin) <> 'object');")
    if int(done) >= TOTAL and not args.force:
        print(f"fixture {FIXTURE} already loaded ({done} captures); nothing to do")
        return
    op = Api(args.api, read_token(tokens, "dev-fixture-operator"))
    authors = [Api(args.api, read_token(tokens, n)) for n in ("dev-producer-sweeper", "dev-producer-agent", "dev-fixture-operator")]

    code, ws = op.call("GET", "/api/v1/workspaces")
    work = next(w["workspace_id"] for w in ws["workspaces"] if w["workspace_key"] == "work")
    initiatives = []
    for n, (title, state) in enumerate([("Synthetic ingress hardening", "ACTIVE"), ("Synthetic DNS cleanup", "BACKLOG"),
                                         ("Synthetic CI runner pools", "BLOCKED"), ("Synthetic mail exposure", "ACTIVE")]):
        body = {"initiative_key": f"dev-fixture-{n}", "workspace_id": work, "title": title,
                "objective": f"Synthetic objective for {title.lower()}.", "work_state": state}
        if state == "BLOCKED":
            body["blocker_summary"] = "Synthetic blocker."
        code, resp = op.call("POST", "/api/v1/initiatives", body, key("initiative", n))
        if code == 409 and (resp or {}).get("detail", {}).get("code") == "INITIATIVE_KEY_EXISTS":
            code, listing = op.call("GET", "/api/v1/initiatives?limit=500")
            resp = next(x for x in listing["initiatives"] if x["initiative_key"] == body["initiative_key"])
        elif code not in (200, 201):
            raise SystemExit(f"initiative {n}: HTTP {code} {resp}")
        initiatives.append(resp["initiative_id"])
        for a, (atype, text) in enumerate([("NOTE", "Synthetic progress note."),
                                           ("OBSERVATION", "Synthetic observation."),
                                           ("DECISION_REQUIRED", "Synthetic decision request.")]):
            code, resp = op.call("POST", f"/api/v1/initiatives/{initiatives[-1]}/activities",
                                 {"activity_type": atype, "body": text}, key("activity", n, a))
            if code not in (200, 201):
                raise SystemExit(f"activity {n}/{a}: HTTP {code} {resp}")

    def create(i: int) -> str:
        text, origin = body_for(i)
        api = authors[i % len(authors)]
        code, resp = api.call("POST", "/api/v1/work/captures", {"body": text, "kind": KINDS[i % len(KINDS)], "origin": origin}, key("capture", i))
        if code not in (200, 201):
            raise SystemExit(f"capture {i}: HTTP {code} {resp}")
        return resp["capture_id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(create, range(TOTAL)))
    print(f"captures created or replayed: {len(ids)}")

    def triage(i: int) -> str:
        cid = ids[i]
        note_case = i % 4
        note = [None, "Synthetic triage note.", f"Triage note quoting {GHP} by mistake.", "Note with {<REDACTED:TOKEN:x>}} residue."][note_case]
        bucket = i % 10
        if bucket in (0, 1, 2):          # ~30% attached
            path, body = "attach", {"initiative_id": initiatives[i % len(initiatives)], "note": note}
        elif bucket == 3 and i % 70 == 3:  # a handful promoted
            path, body = "promote", {"initiative_key": f"dev-promoted-{i}", "title": f"Synthetic promoted {i}",
                                     "objective": "Synthetic promoted objective.", "note": note}
        elif bucket in (4, 5):           # ~20% discarded
            path, body = "discard", {"note": note}
        else:
            return "INBOX"
        code, resp = op.call("POST", f"/api/v1/work/captures/{cid}/{path}", body, key("triage", i))
        if code == 409 and isinstance(resp, dict) and resp.get("detail", {}).get("code") == "CAPTURE_ALREADY_TRIAGED":
            return "replayed"
        if code not in (200, 201):
            raise SystemExit(f"triage {i} {path}: HTTP {code} {resp}")
        return path

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(triage, range(TOTAL)))
    print("triage:", {o: outcomes.count(o) for o in sorted(set(outcomes))})

    # Historical shape the API cannot create. Disposable dev database only (marker-guarded).
    sql(args.sql_runner, f"""
BEGIN;
WITH f AS (
  SELECT capture_id, (origin->>'seq')::int AS seq FROM work.captures WHERE origin->>'fixture' = '{FIXTURE}')
UPDATE work.captures c
   SET created_at = timestamptz '2026-08-01 00:00:00+00' + make_interval(mins => (f.seq / 25) * 97)
  FROM f WHERE c.capture_id = f.capture_id;          -- blocks of 25 share one exact timestamp
UPDATE work.captures SET origin = to_jsonb('legacy free-form origin with ' || '{AKIA}'::text)
 WHERE origin->>'fixture' = '{FIXTURE}' AND (origin->>'seq')::int % 250 = 11;
UPDATE work.captures SET origin = jsonb_build_array('legacy', 'array', 'origin')
 WHERE jsonb_typeof(origin) = 'object' AND origin->>'fixture' = '{FIXTURE}' AND (origin->>'seq')::int % 250 = 12;
-- pre-#171 activity bodies were never write-time refused
INSERT INTO work.initiative_activities (initiative_id, author_principal_id, idempotency_key, activity_type, body, created_at)
SELECT i.initiative_id, i.owner_principal_id, 'dev-fixture-legacy-activity', 'OBSERVATION',
       'Legacy synthetic observation quoting {AKIA} in prose.', timestamptz '2026-08-02 00:00:00+00'
  FROM work.initiatives i WHERE i.initiative_key = 'dev-fixture-0'
ON CONFLICT DO NOTHING;
COMMIT;
""")
    print(sql(args.sql_runner, "SELECT status || '=' || count(*) FROM work.captures GROUP BY status ORDER BY status;").replace("\n", " "))
    print("tied timestamp groups:", sql(args.sql_runner, "SELECT count(*) FROM (SELECT created_at FROM work.captures GROUP BY 1 HAVING count(*) > 1) t;"))


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("provision"); a.add_argument("--api", required=True); a.add_argument("--env-file", required=True); a.add_argument("--tokens", required=True)
    b = sub.add_parser("load"); b.add_argument("--api", required=True); b.add_argument("--tokens", required=True)
    b.add_argument("--sql-runner", required=True); b.add_argument("--force", action="store_true")
    args = p.parse_args()
    {"provision": provision, "load": load}[args.cmd](args)


if __name__ == "__main__":
    main()
