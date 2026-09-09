"""SECRETS-V3 `_FILE` secret-source contract for DocPlane consumers.

WHY THIS EXISTS
---------------
Delivering a bearer token through an ordinary environment variable means the plaintext is
visible in the unit's `EnvironmentFile`, in `/proc/<pid>/environ`, in `systemctl show
--property=Environment`, and to anything that dumps the process environment. SECRETS-V3
replaces that with a protected runtime FILE whose PATH -- not whose value -- is configured.

THE CONTRACT
------------
For a logical secret named `NAME`:

    <NAME>_FILE set   -> read the file. It WINS, unconditionally.
    <NAME> set only   -> accept, emit a deprecation warning. Transitional only.
    both set          -> <NAME>_FILE wins; the env value is ignored entirely.
    neither           -> hard failure.

And the rule that carries the actual security weight:

    <NAME>_FILE set but unreadable/empty -> HARD FAILURE. Never fall back to <NAME>.

That last rule is the point of the whole module. Once an operator has deliberately configured
file-based delivery, a materialization failure must NOT silently resurrect legacy plaintext
delivery -- otherwise the migration can appear complete while the old path is still load-bearing,
and a rotation that revokes the old credential would fail in a way nobody predicted.

REDACTION
---------
No exception, warning or log line raised here contains the secret value. Messages carry the
variable NAME and the FILE PATH only. `tests/test_secret_source.py` asserts this against a
sentinel value for every failure mode.
"""
from __future__ import annotations

import os
import warnings
from pathlib import Path

__all__ = ["read_secret", "SecretSourceError", "SecretSourceDeprecation", "describe_source"]


class SecretSourceError(RuntimeError):
    """Fail-closed secret resolution error. Never carries the secret value."""


class SecretSourceDeprecation(DeprecationWarning):
    """Legacy plaintext-environment delivery was used for a governed secret."""


def _file_var(name: str) -> str:
    return f"{name}_FILE"


def read_secret(name: str, *, allow_env_fallback: bool = True) -> str:
    """Resolve one governed secret. See the module docstring for the full contract.

    `allow_env_fallback=False` enforces the TERMINAL migration state: the file is the only
    accepted source and a bare `<NAME>` is refused outright.
    """
    file_var = _file_var(name)
    path_value = os.environ.get(file_var, "").strip()

    if path_value:
        # The file path is configured: it is now the ONLY source. Any problem is fatal.
        path = Path(path_value)
        try:
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise SecretSourceError(
                f"{file_var} points to {path_value!r} which does not exist; refusing to fall "
                f"back to {name}. A missing runtime secret file means materialization did not "
                f"run or did not complete."
            ) from exc
        except PermissionError as exc:
            raise SecretSourceError(
                f"{file_var} points to {path_value!r} which is not readable by this process "
                f"(uid={os.geteuid()}); refusing to fall back to {name}."
            ) from exc
        except OSError as exc:
            # errno only -- str(exc) on an OSError carries the path, never file CONTENT.
            raise SecretSourceError(
                f"{file_var} points to {path_value!r} which could not be read "
                f"(errno={exc.errno}); refusing to fall back to {name}."
            ) from exc

        try:
            value = raw.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise SecretSourceError(
                f"{file_var} points to {path_value!r} whose contents are not valid UTF-8; "
                f"refusing to fall back to {name}."
            ) from exc

        if not value:
            raise SecretSourceError(
                f"{file_var} points to {path_value!r} which is empty; refusing to fall back "
                f"to {name}. An empty secret file is a materialization failure, not an "
                f"instruction to use the legacy value."
            )
        return value

    env_value = os.environ.get(name, "").strip()
    if env_value:
        if not allow_env_fallback:
            raise SecretSourceError(
                f"{name} was supplied through the environment but this secret has reached the "
                f"TERMINAL migration state; set {file_var} instead."
            )
        warnings.warn(
            f"{name} was read from the plaintext environment. SECRETS-V3 delivers this secret "
            f"through {file_var}; plaintext environment delivery is deprecated and will be "
            f"removed.",
            SecretSourceDeprecation,
            stacklevel=2,
        )
        return env_value

    raise SecretSourceError(
        f"neither {file_var} nor {name} is set; refusing to fall back to another principal."
    )


def describe_source(name: str) -> dict:
    """Non-secret description of WHERE this secret would come from. For diagnostics only.

    Never returns or reveals the value -- only which source is configured and, for the file
    source, whether it is present and readable.
    """
    file_var = _file_var(name)
    path_value = os.environ.get(file_var, "").strip()
    if path_value:
        p = Path(path_value)
        return {
            "name": name,
            "source": "file",
            "variable": file_var,
            "path": path_value,
            "present": p.exists(),
            "readable": os.access(path_value, os.R_OK),
            "size": p.stat().st_size if p.exists() else None,
        }
    if os.environ.get(name, "").strip():
        return {"name": name, "source": "environment", "variable": name, "deprecated": True}
    return {"name": name, "source": None}
