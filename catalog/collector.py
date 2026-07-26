from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from typing import Any

import yaml


_SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_SENSITIVE_KEYS = {
    "credential",
    "credentials",
    "dsn",
    "pass",
    "passwd",
    "password",
    "secret",
    "token",
}


class CatalogError(RuntimeError):
    """A collection input or invariant was invalid."""


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _load_json(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(raw, dict):
        raise CatalogError(f"expected JSON object in {path}")
    return raw


def _load_source(registry_path: pathlib.Path, source_name: str) -> dict[str, Any]:
    if not _SOURCE_RE.fullmatch(source_name):
        raise CatalogError("source name must match ^[a-z0-9][a-z0-9_-]{0,62}$")

    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    sources = registry.get("sources")
    if not isinstance(sources, list):
        raise CatalogError("registry must contain a sources list")

    matches = [item for item in sources if isinstance(item, dict) and item.get("name") == source_name]
    if len(matches) != 1:
        raise CatalogError(f"registry must define source {source_name!r} exactly once")

    source = dict(matches[0])
    for key in ("service", "owner", "dsnEnv"):
        value = source.get(key)
        if not isinstance(value, str) or not value.strip():
            raise CatalogError(f"source {source_name!r} requires non-empty {key}")

    for key in ("include", "exclude"):
        value = source.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise CatalogError(f"source {source_name!r} {key} must be a list of strings")
        source[key] = list(value)

    labels = source.get("labels", {})
    if not isinstance(labels, dict) or not all(
        isinstance(key, str) and isinstance(value, (str, int, float, bool))
        for key, value in labels.items()
    ):
        raise CatalogError(f"source {source_name!r} labels must be a scalar mapping")
    source["labels"] = {str(key): str(value) for key, value in labels.items()}
    return source


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if str(key).lower() in _SENSITIVE_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _run(command: list[str], *, cwd: pathlib.Path, env: dict[str, str], dsn: str) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = (completed.stdout or "").replace(dsn, "[redacted-dsn]")
    if completed.returncode != 0:
        tail = output[-2000:]
        raise CatalogError(f"command failed ({completed.returncode}): {' '.join(command)}\n{tail}")
    return output


def _tbls_version(tbls: str, *, cwd: pathlib.Path, env: dict[str, str], dsn: str) -> str:
    output = _run([tbls, "version"], cwd=cwd, env=env, dsn=dsn).strip()
    return output.splitlines()[-1] if output else "unknown"


def _write_tbls_config(path: pathlib.Path, source: dict[str, Any], docs_path: pathlib.Path) -> None:
    dsn_env = source["dsnEnv"]
    config: dict[str, Any] = {
        "dsn": "${%s}" % dsn_env,
        "docPath": str(docs_path),
        "format": {
            "sort": True,
            "showOnlyFirstParagraph": True,
            "hideColumnsWithoutValues": True,
        },
    }
    if source["include"]:
        config["include"] = source["include"]
    if source["exclude"]:
        config["exclude"] = source["exclude"]
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _canonical_schema(schema_path: pathlib.Path, dsn: str) -> bytes:
    raw_text = schema_path.read_text(encoding="utf-8")
    if dsn and dsn in raw_text:
        raise CatalogError("tbls schema output contains the live DSN")
    parsed = json.loads(raw_text)
    sanitized = _redact(parsed)
    canonical = json.dumps(sanitized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if dsn and dsn in canonical:
        raise CatalogError("sanitized schema output still contains the live DSN")
    return (canonical + "\n").encode("utf-8")


@contextlib.contextmanager
def _source_lock(source_dir: pathlib.Path):
    source_dir.mkdir(parents=True, exist_ok=True)
    lock_path = source_dir / "collect.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def collect_source(
    *,
    registry_path: pathlib.Path,
    source_name: str,
    data_dir: pathlib.Path,
    tbls: str = "tbls",
) -> dict[str, Any]:
    source = _load_source(registry_path, source_name)
    dsn_env = source["dsnEnv"]
    dsn = os.environ.get(dsn_env, "")
    if not dsn:
        raise CatalogError(f"required DSN environment variable {dsn_env} is not set")

    source_dir = data_dir / source_name
    status_path = source_dir / "status.json"

    with _source_lock(source_dir):
        try:
            with tempfile.TemporaryDirectory(prefix=f"docplane-catalog-{source_name}-") as tmp_raw:
                tmp = pathlib.Path(tmp_raw)
                docs_path = tmp / "docs"
                schema_path = tmp / "schema.json"
                _write_tbls_config(tmp / ".tbls.yml", source, docs_path)

                env = dict(os.environ)
                version = _tbls_version(tbls, cwd=tmp, env=env, dsn=dsn)
                _run([tbls, "out", "-t", "json", "-o", str(schema_path)], cwd=tmp, env=env, dsn=dsn)
                _run([tbls, "doc", "--rm-dist"], cwd=tmp, env=env, dsn=dsn)

                canonical = _canonical_schema(schema_path, dsn)
                fingerprint = hashlib.sha256(canonical).hexdigest()
                previous = _load_json(source_dir / "latest.json")
                previous_fingerprint = previous.get("fingerprint") if previous else None

                manifest = {
                    "catalog_contract_version": "1",
                    "source": source_name,
                    "service": source["service"],
                    "owner": source["owner"],
                    "labels": source["labels"],
                    "collected_at": _utc_now(),
                    "collector": "tbls",
                    "collector_version": version,
                    "fingerprint": fingerprint,
                    "previous_fingerprint": previous_fingerprint,
                    "schema_path": "schema.json",
                    "docs_path": "docs",
                }

                snapshot_dir = source_dir / "snapshots" / fingerprint
                if snapshot_dir.exists():
                    existing = (snapshot_dir / "schema.json").read_bytes()
                    if existing != canonical:
                        raise CatalogError(
                            "fingerprint collision or mutable snapshot: existing schema bytes differ"
                        )
                else:
                    staging = source_dir / "snapshots" / (fingerprint + ".tmp")
                    if staging.exists():
                        shutil.rmtree(staging)
                    staging.mkdir(parents=True)
                    (staging / "schema.json").write_bytes(canonical)
                    if docs_path.exists():
                        shutil.copytree(docs_path, staging / "docs")
                    _atomic_json(staging / "manifest.json", manifest)
                    os.replace(staging, snapshot_dir)

                latest = {
                    "source": source_name,
                    "fingerprint": fingerprint,
                    "collected_at": manifest["collected_at"],
                    "manifest_path": f"snapshots/{fingerprint}/manifest.json",
                }
                _atomic_json(source_dir / "latest.json", latest)
                _atomic_json(
                    status_path,
                    {
                        "source": source_name,
                        "status": "current",
                        "checked_at": manifest["collected_at"],
                        "fingerprint": fingerprint,
                        "error": None,
                    },
                )
                return manifest
        except Exception as exc:
            message = str(exc).replace(dsn, "[redacted-dsn]")
            _atomic_json(
                status_path,
                {
                    "source": source_name,
                    "status": "failed",
                    "checked_at": _utc_now(),
                    "fingerprint": (_load_json(source_dir / "latest.json") or {}).get("fingerprint"),
                    "error": {"type": type(exc).__name__, "message": message[:4000]},
                },
            )
            raise


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect one DocPlane schema-catalog source")
    parser.add_argument("--registry", type=pathlib.Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument(
        "--data-dir",
        type=pathlib.Path,
        default=pathlib.Path(os.environ.get("DOCPLANE_CATALOG_DATA", "/var/lib/docplane/catalog")),
    )
    parser.add_argument("--tbls", default=os.environ.get("TBLS_BIN", "tbls"))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    manifest = collect_source(
        registry_path=args.registry,
        source_name=args.source,
        data_dir=args.data_dir,
        tbls=args.tbls,
    )
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
