#!/usr/bin/env python3
"""Export WORK-collaborator credential expiry as node_exporter textfile metrics.

The WORK inbox's participant tokens and collaboration grants expire, and an
expired one turns every inbox call into a 401/403 with nothing announcing it.
This producer publishes, per ACTIVE WORK_COLLABORATOR participant:

  docplane_work_collaborator_active_tokens{participant}
  docplane_work_collaborator_lapsed_tokens{participant}
  docplane_work_collaborator_token_earliest_expiry_timestamp_seconds{participant}
      (only when the participant has an active token with an expiry)
  docplane_work_collaborator_grants{participant,expiry="dated"|"never"}
  docplane_work_collaborator_grant_expiry_timestamp_seconds{participant,initiative_key}
      (every unrevoked grant with a non-NULL expiry, including lapsed ones)

and, on every run, its own liveness:

  docplane_work_credential_expiry_probe_ok              1 = the read succeeded
  docplane_work_credential_expiry_last_run_timestamp_seconds

Signal & Alert Contract (DocPlane standards/07-health-check-pattern.md):
  * A failed read writes probe_ok 0 and NO participant series, so a broken read
    is visible as failure rather than as "no expiring credentials".
  * The file is written to a temp file, chmod 0644 and renamed into place.
  * No inline sudo. The systemd unit runs as root, which is the privilege the
    `docker exec` read needs.

Read path: `docker exec` into the DocPlane PostgreSQL container (resolved by
compose labels, never by a hard-coded name or address) and run psql over the
container-local socket as the password-less, read-only role
`docplane_credential_expiry_observer`, which can SELECT only the two value-blind
views from migration 021. No credential is read, held or passed: no .env, no
container environment, no bootstrap token. Labels are display names and
initiative keys only, never a token id, hash or prefix.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

DEFAULT_METRICS_FILE = "/var/lib/node_exporter/textfile_collector/docplane_work_credential_expiry.prom"
OBSERVER_ROLE = "docplane_credential_expiry_observer"

QUERY = """
SELECT json_build_object(
  'tokens', (SELECT coalesce(json_agg(json_build_object(
      'participant', participant,
      'active_tokens', active_tokens,
      'lapsed_tokens', lapsed_tokens,
      'earliest_active_expiry', extract(epoch FROM earliest_active_expiry))), '[]'::json)
    FROM work.collaborator_token_expiry),
  'grants', (SELECT coalesce(json_agg(json_build_object(
      'participant', participant,
      'initiative_key', initiative_key,
      'expires_at', extract(epoch FROM expires_at))), '[]'::json)
    FROM work.collaborator_grant_expiry)
)
"""


class ProbeError(RuntimeError):
    """A read or validation failure. The message never carries query output."""


def _run(argv: list[str], *, stdin: str | None = None, timeout: int = 30) -> str:
    try:
        result = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(f"{argv[0]} {argv[1]} could not run: {type(exc).__name__}") from None
    if result.returncode != 0:
        # stderr from docker/psql carries no credential on this path (none is used),
        # but keep only its first line so a large dump cannot reach the journal.
        first = (result.stderr.strip().splitlines() or ["(no stderr)"])[0][:200]
        raise ProbeError(f"{argv[0]} {argv[1]} exited {result.returncode}: {first}")
    return result.stdout


def resolve_container(project: str, service: str) -> str:
    out = _run([
        "docker", "ps",
        "--filter", f"label=com.docker.compose.project={project}",
        "--filter", f"label=com.docker.compose.service={service}",
        "--format", "{{.ID}}",
    ])
    ids = [line for line in out.splitlines() if line.strip()]
    if len(ids) != 1:
        raise ProbeError(f"expected exactly one {project}/{service} container, found {len(ids)}")
    return ids[0]


def read_source(container: str, database: str) -> dict:
    out = _run([
        "docker", "exec", "-i", container,
        "psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1",
        "-U", OBSERVER_ROLE, "-d", database,
    ], stdin=QUERY)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        raise ProbeError("source returned unparseable output") from None


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _number(value) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def render(source: dict | None, now: int) -> str:
    """Render the textfile. `source` None means the probe failed."""
    lines: list[str] = []

    def family(name: str, help_text: str, samples: list[tuple[dict[str, str], object]]) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        for labels, value in samples:
            rendered = ",".join(f'{k}="{_label(v)}"' for k, v in labels.items())
            lines.append(f"{name}{{{rendered}}} {_number(value)}" if rendered else f"{name} {_number(value)}")

    if source is not None:
        tokens = source["tokens"]
        grants = source["grants"]
        family(
            "docplane_work_collaborator_active_tokens",
            "Unrevoked, unexpired DocPlane API tokens held by an ACTIVE WORK_COLLABORATOR participant.",
            [({"participant": t["participant"]}, t["active_tokens"]) for t in tokens],
        )
        family(
            "docplane_work_collaborator_lapsed_tokens",
            "Unrevoked DocPlane API tokens of the participant whose expiry has passed.",
            [({"participant": t["participant"]}, t["lapsed_tokens"]) for t in tokens],
        )
        family(
            "docplane_work_collaborator_token_earliest_expiry_timestamp_seconds",
            "Unix time of the earliest expiry among the participant's active tokens; absent when it holds none with an expiry.",
            [({"participant": t["participant"]}, t["earliest_active_expiry"])
             for t in tokens if t["earliest_active_expiry"] is not None],
        )
        counts: dict[str, dict[str, int]] = {t["participant"]: {"dated": 0, "never": 0} for t in tokens}
        for g in grants:
            bucket = counts.setdefault(g["participant"], {"dated": 0, "never": 0})
            bucket["never" if g["expires_at"] is None else "dated"] += 1
        family(
            "docplane_work_collaborator_grants",
            "Unrevoked WORK collaboration grants of the participant, by whether they carry an expiry (dated) or not (never).",
            [({"participant": p, "expiry": kind}, n) for p in sorted(counts) for kind, n in sorted(counts[p].items())],
        )
        family(
            "docplane_work_collaborator_grant_expiry_timestamp_seconds",
            "Unix time a dated, unrevoked collaboration grant expires; stays present (in the past) after it lapses until renewed or revoked.",
            [({"participant": g["participant"], "initiative_key": g["initiative_key"]}, g["expires_at"])
             for g in grants if g["expires_at"] is not None],
        )
    family(
        "docplane_work_credential_expiry_probe_ok",
        "1 when this run read the credential-expiry views; 0 when the read or validation failed (participant series are then omitted).",
        [({}, 1 if source is not None else 0)],
    )
    family(
        "docplane_work_credential_expiry_last_run_timestamp_seconds",
        "Unix time this producer last ran, successful or not.",
        [({}, now)],
    )
    return "\n".join(lines) + "\n"


def validate(source: dict) -> None:
    """Refuse output whose label sets would collide; Prometheus would reject the whole file."""
    if not isinstance(source, dict) or not isinstance(source.get("tokens"), list) or not isinstance(source.get("grants"), list):
        raise ProbeError("source shape is not {tokens: [...], grants: [...]}")
    token_fields = {"participant", "active_tokens", "lapsed_tokens", "earliest_active_expiry"}
    grant_fields = {"participant", "initiative_key", "expires_at"}
    for rows, fields in ((source["tokens"], token_fields), (source["grants"], grant_fields)):
        for row in rows:
            if not isinstance(row, dict) or set(row) != fields:
                raise ProbeError("a source row does not have the expected fields")
    names = [t["participant"] for t in source["tokens"]]
    if len(names) != len(set(names)):
        raise ProbeError("two ACTIVE WORK_COLLABORATOR principals share a display name; labels would be ambiguous")
    keys = [(g["participant"], g["initiative_key"]) for g in source["grants"]]
    if len(keys) != len(set(keys)):
        raise ProbeError("a (participant, initiative_key) pair is not unique; labels would be ambiguous")
    for g in source["grants"]:
        if g["participant"] not in set(names):
            raise ProbeError("a grant belongs to a participant missing from the token view")


def write_atomically(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    try:
        temporary.chmod(0o644)  # NamedTemporaryFile is 0600, which node_exporter silently skips
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--metrics-file", default=DEFAULT_METRICS_FILE)
    parser.add_argument("--compose-project", default="docplane")
    parser.add_argument("--compose-service", default="postgres")
    parser.add_argument("--database", default="docs")
    parser.add_argument("--stdout", action="store_true", help="print the textfile instead of writing it")
    args = parser.parse_args(argv)

    now = int(time.time())
    source = None
    status = 0
    try:
        container = resolve_container(args.compose_project, args.compose_service)
        source = read_source(container, args.database)
        validate(source)
    except ProbeError as exc:
        print(f"work-credential-expiry: probe failed: {exc}", file=sys.stderr)
        source, status = None, 1
    except (KeyError, TypeError, ValueError) as exc:
        print(f"work-credential-expiry: probe failed: malformed source row ({type(exc).__name__})", file=sys.stderr)
        source, status = None, 1

    content = render(source, now)
    if args.stdout:
        sys.stdout.write(content)
    else:
        write_atomically(Path(args.metrics_file), content)
    if source is not None:
        print(
            f"work-credential-expiry: ok participants={len(source['tokens'])} grants={len(source['grants'])} "
            f"at={datetime.fromtimestamp(now).isoformat(timespec='seconds')}",
            file=sys.stderr,
        )
    # Non-zero on a failed probe so systemd also records it; the metrics file is written either way.
    return status


if __name__ == "__main__":
    sys.exit(main())
