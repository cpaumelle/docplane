"""WORK-collaborator credential-expiry producer and its value-blind read surface.

Two layers:
  * the producer (scripts/work_credential_expiry_metrics.py) against a fake `docker`
    on PATH: series shape, liveness on success and failure, atomic 0644 write, and
    refusal of ambiguous labels;
  * migration 021 against PostgreSQL (skipped without DB_HOST): view semantics for
    active / lapsed / revoked / non-expiring tokens and grants, and the observer
    role's privilege boundary (views readable, base tables and writes refused).
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "work_credential_expiry_metrics.py"
sys.path.insert(0, str(ROOT / "scripts"))

import work_credential_expiry_metrics as producer  # noqa: E402

SOURCE = {
    "tokens": [
        {"participant": "chatgpt-architect", "active_tokens": 1, "lapsed_tokens": 0, "earliest_active_expiry": 1802718859.051702},
        {"participant": "claude-code", "active_tokens": 0, "lapsed_tokens": 0, "earliest_active_expiry": None},
    ],
    "grants": [
        {"participant": "chatgpt-architect", "initiative_key": "docplane-work-agent-notifications", "expires_at": None},
        {"participant": "claude-code", "initiative_key": "docplane-work-agent-notifications", "expires_at": 1797534858.0},
    ],
}


def _fake_docker(tmp_path: Path, payload: str, *, exit_code: int = 0, containers: int = 1) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "payload.json").write_text(payload)
    ids = "\\n".join(f"c{i}" for i in range(containers))
    script = bin_dir / "docker"
    script.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = ps ]; then printf "{ids}\\n"; exit 0; fi\n'
        f'if [ "$1" = exec ]; then cat >/dev/null; cat "{tmp_path / "payload.json"}"; exit {exit_code}; fi\n'
        "exit 2\n"
    )
    script.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}


def _run(tmp_path: Path, env: dict[str, str]) -> tuple[int, str]:
    target = tmp_path / "out" / "docplane_work_credential_expiry.prom"
    result = subprocess.run([sys.executable, str(SCRIPT), "--metrics-file", str(target)], env=env,
                            capture_output=True, text=True, check=False)
    return result.returncode, target.read_text()


def test_success_publishes_participant_series_and_liveness(tmp_path):
    code, text = _run(tmp_path, _fake_docker(tmp_path, json.dumps(SOURCE)))
    assert code == 0
    assert 'docplane_work_collaborator_active_tokens{participant="chatgpt-architect"} 1' in text
    assert 'docplane_work_collaborator_active_tokens{participant="claude-code"} 0' in text
    assert 'docplane_work_collaborator_lapsed_tokens{participant="claude-code"} 0' in text
    assert 'docplane_work_collaborator_token_earliest_expiry_timestamp_seconds{participant="chatgpt-architect"} 1802718859.051702' in text
    # No active token with an expiry: no expiry series (the count series says why).
    assert 'token_earliest_expiry_timestamp_seconds{participant="claude-code"}' not in text
    assert 'docplane_work_collaborator_grants{participant="chatgpt-architect",expiry="never"} 1' in text
    assert 'docplane_work_collaborator_grants{participant="chatgpt-architect",expiry="dated"} 0' in text
    assert ('docplane_work_collaborator_grant_expiry_timestamp_seconds{participant="claude-code",'
            'initiative_key="docplane-work-agent-notifications"} 1797534858') in text
    # A NULL-expiry grant has no expiry series.
    assert 'grant_expiry_timestamp_seconds{participant="chatgpt-architect"' not in text
    assert 'docplane_work_collaborator_grant_active{participant="chatgpt-architect",initiative_key="docplane-work-agent-notifications"} 1' in text
    assert "docplane_work_credential_expiry_probe_ok 1" in text
    assert "docplane_work_credential_expiry_last_run_timestamp_seconds " in text
    # Every family is declared; no producer-set job/instance label.
    assert text.count("# TYPE") == text.count("# HELP") == 8
    assert "job=" not in text and "instance=" not in text


def test_written_file_is_collector_readable_and_complete(tmp_path):
    _run(tmp_path, _fake_docker(tmp_path, json.dumps(SOURCE)))
    target = tmp_path / "out" / "docplane_work_credential_expiry.prom"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert [p.name for p in target.parent.iterdir()] == [target.name]


@pytest.mark.parametrize("payload,exit_code,containers", [
    ("not json", 0, 1),                                  # unparseable output
    (json.dumps(SOURCE), 2, 1),                           # psql/docker exec failed
    (json.dumps(SOURCE), 0, 0),                           # no database container
    (json.dumps(SOURCE), 0, 2),                           # ambiguous container
    (json.dumps({"tokens": SOURCE["tokens"] * 2, "grants": []}), 0, 1),  # duplicate display name
    (json.dumps({"tokens": [{"participant": "x"}], "grants": []}), 0, 1),  # malformed row
])
def test_failure_is_probe_ok_zero_with_no_participant_series(tmp_path, payload, exit_code, containers):
    code, text = _run(tmp_path, _fake_docker(tmp_path, payload, exit_code=exit_code, containers=containers))
    assert code == 1
    assert "docplane_work_credential_expiry_probe_ok 0" in text
    assert "docplane_work_credential_expiry_last_run_timestamp_seconds " in text
    assert "docplane_work_collaborator_" not in text


def test_label_values_are_escaped():
    text = producer.render({"tokens": [{"participant": 'a"b\\c', "active_tokens": 1, "lapsed_tokens": 0,
                                         "earliest_active_expiry": None}], "grants": []}, 0)
    assert 'participant="a\\"b\\\\c"' in text


# --------------------------------------------------------------------------- database
db = pytest.mark.skipif(not os.environ.get("DB_HOST"), reason="requires a migrated PostgreSQL (set DB_HOST etc.)")
ROLE = "docplane_credential_expiry_observer"


@pytest.fixture
def seeded():
    from app.db import get_conn

    tag = uuid.uuid4().hex[:10]
    names = {k: f"credexp-{k}-{tag}" for k in ("live", "none", "gone", "suspended")}
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT workspace_id FROM docplane.workspaces WHERE workspace_key='work'")
        workspace = cur.fetchone()[0]
        ids = {}
        for key, name in names.items():
            status = "SUSPENDED" if key == "suspended" else "ACTIVE"
            cur.execute(
                "INSERT INTO docplane.principals(display_name,principal_kind,authorization_mode,status) "
                "VALUES (%s,'AGENT','WORK_COLLABORATOR',%s) RETURNING principal_id", (name, status))
            ids[key] = cur.fetchone()[0]

        def token(pid, *, expires: str | None, revoked: bool = False, issued: str = "now() - interval '100 days'"):
            cur.execute(
                f"INSERT INTO docplane.api_tokens(principal_id,token_hash,token_prefix,issued_at,expires_at,revoked_at) "
                f"VALUES (%s,%s,'fixture',{issued},{expires or 'NULL'},{'now()' if revoked else 'NULL'})",
                (pid, hashlib.sha256(uuid.uuid4().bytes).hexdigest()))

        token(ids["live"], expires="now() + interval '30 days'")
        token(ids["live"], expires="now() + interval '5 days'")
        token(ids["live"], expires="now() + interval '1 day'", revoked=True)   # revoked: ignored
        token(ids["live"], expires="now() - interval '1 day'")                  # lapsed
        token(ids["none"], expires=None)                                          # non-expiring
        token(ids["gone"], expires="now() - interval '2 days'")                  # only a lapsed token
        token(ids["suspended"], expires="now() + interval '3 days'")
        cur.execute(
            "INSERT INTO work.initiatives (initiative_key,workspace_id,title,objective,owner_principal_id,idempotency_key) "
            "VALUES (%s,%s,'credexp','credexp',%s,%s) RETURNING initiative_id",
            (f"credexp-{tag}", workspace, ids["live"], str(uuid.uuid4())))
        initiative = cur.fetchone()[0]
        grants = [("live", "now() + interval '10 days'", False), ("none", None, False),
                  ("gone", "now() - interval '1 hour'", False), ("suspended", "now() + interval '1 day'", False)]
        for key, expires, revoked in grants:
            cur.execute(
                f"INSERT INTO work.collaboration_grants(initiative_id,principal_id,participant_role,expires_at,revoked_at) "
                f"VALUES (%s,%s,'AGENT',{expires or 'NULL'},{'now()' if revoked else 'NULL'})", (initiative, ids[key]))
        conn.commit()
    yield names, f"credexp-{tag}"


def _as_observer(sql: str, params=()):
    from app.db import get_conn

    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(f"SET ROLE {ROLE}")
            cur.execute(sql, params)
            return cur.fetchall()
        finally:
            conn.rollback()


@db
def test_token_view_semantics(seeded):
    names, _ = seeded
    rows = {r[0]: r[1:] for r in _as_observer(
        "SELECT participant, active_tokens, lapsed_tokens, earliest_active_expiry IS NOT NULL, "
        "round(extract(epoch FROM earliest_active_expiry - now()) / 86400) "
        "FROM work.collaborator_token_expiry WHERE participant LIKE 'credexp-%%'")}
    assert rows[names["live"]] == (2, 1, True, 5)       # earliest ACTIVE expiry; revoked/lapsed ignored
    assert rows[names["none"]] == (1, 0, False, None)   # non-expiring: active but no expiry
    assert rows[names["gone"]] == (0, 1, False, None)   # no valid token, one lapsed
    assert names["suspended"] not in rows               # not an ACTIVE participant


@db
def test_grant_view_keeps_lapsed_and_null_and_drops_inactive(seeded):
    names, key = seeded
    rows = {r[0]: r[1] for r in _as_observer(
        "SELECT participant, expires_at IS NULL OR expires_at > now() FROM work.collaborator_grant_expiry "
        "WHERE initiative_key = %s", (key,))}
    assert rows == {names["live"]: True, names["none"]: True, names["gone"]: False}


@db
def test_producer_query_runs_as_observer(seeded):
    names, _ = seeded
    (payload,), = _as_observer(producer.QUERY)
    data = payload if isinstance(payload, dict) else json.loads(payload)
    mine = [t for t in data["tokens"] if t["participant"] == names["live"]]
    assert mine and mine[0]["active_tokens"] == 2 and isinstance(mine[0]["earliest_active_expiry"], (int, float))


@db
def test_views_expose_no_token_identity():
    rows = _as_observer(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema='work' AND table_name IN ('collaborator_token_expiry','collaborator_grant_expiry')")
    columns = {c for _, c in rows}
    assert columns == {"participant", "active_tokens", "lapsed_tokens", "earliest_active_expiry",
                       "initiative_key", "participant_role", "expires_at"}


@db
@pytest.mark.parametrize("sql", [
    "SELECT token_hash FROM docplane.api_tokens LIMIT 1",
    "SELECT display_name FROM docplane.principals LIMIT 1",
    "SELECT 1 FROM work.collaboration_grants LIMIT 1",
    "SELECT 1 FROM docs.pages LIMIT 1",
])
def test_observer_cannot_read_base_tables(sql):
    import psycopg2

    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _as_observer(sql)


@db
def test_observer_role_is_password_less_read_only_and_unprivileged():
    from app.db import get_conn

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT a.rolcanlogin, a.rolsuper, a.rolcreaterole, a.rolcreatedb, a.rolreplication, a.rolbypassrls, "
                    "a.rolpassword IS NULL, r.rolconfig FROM pg_authid a JOIN pg_roles r USING (oid) WHERE a.rolname = %s", (ROLE,))
        login, superuser, createrole, createdb, replication, bypassrls, no_password, config = cur.fetchone()
    assert login and no_password
    assert not (superuser or createrole or createdb or replication or bypassrls)
    assert "default_transaction_read_only=on" in config


def test_source_explicitly_selects_container_unix_socket(monkeypatch):
    captured = {}
    def fake(argv, **kwargs):
        captured["argv"] = argv
        return json.dumps({"tokens": [], "grants": []})
    monkeypatch.setattr(producer, "_run", fake)
    assert producer.read_source("fixture-container", "docs") == {"tokens": [], "grants": []}
    argv = captured["argv"]
    assert argv[argv.index("-h") + 1] == "/var/run/postgresql"


def test_grant_active_distinguishes_lapsed_from_nonexpiring():
    text = producer.render({"tokens": [], "grants": [
        {"participant": "p", "initiative_key": "old", "expires_at": 100},
        {"participant": "p", "initiative_key": "permanent", "expires_at": None},
    ]}, 101)
    assert 'docplane_work_collaborator_grant_active{participant="p",initiative_key="old"} 0' in text
    assert 'docplane_work_collaborator_grant_active{participant="p",initiative_key="permanent"} 1' in text
