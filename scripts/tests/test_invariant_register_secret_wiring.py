"""Invariant-register consumer wiring: invariant_register.py must resolve its bearer through the SECRETS-V3
`_FILE` contract, not through the plaintext environment.

Same shape as test_work_catalogue_secret_wiring.py — the second consumer inherits the contract
rather than re-deciding it. Scheduling this generator is what makes it concrete: a timer's
EnvironmentFile puts a plaintext bearer in /proc/<pid>/environ on every tick. (It does NOT
appear in `systemctl show --property=Environment`, which exposes only the file path — an
earlier version of this docstring said otherwise.) Production moved to file delivery on
2026-09-23; these tests pin the wiring so it cannot regress to the environment.

Deliberately INERT: never contacts DocPlane, never needs the real credential.
"""
import ast
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

TARGET = SCRIPTS / "invariant_register.py"
TOKEN_VAR = "DOCPLANE_INVARIANT_REGISTER_TOKEN"


def _tree():
    return ast.parse(TARGET.read_text())


def _calls(tree):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _environ_subscripts(tree):
    """`os.environ["NAME"]` reads, which is how this module takes configuration."""
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "environ"
                and isinstance(node.slice, ast.Constant)):
            found.append(node.slice.value)
    return found


def test_token_is_read_through_read_secret():
    read_secret_args = [
        n.args[0].value for n in _calls(_tree())
        if isinstance(n.func, ast.Name) and n.func.id == "read_secret"
        and n.args and isinstance(n.args[0], ast.Constant)
    ]
    assert TOKEN_VAR in read_secret_args, "the importer bearer is not resolved through read_secret()"


def test_token_is_not_read_from_the_plaintext_environment():
    names = _environ_subscripts(_tree())
    assert TOKEN_VAR not in names, (
        f"{TOKEN_VAR} is still read from os.environ; SECRETS-V3 requires the `_FILE` contract "
        f"for governed secrets")
    # Non-secret configuration legitimately still comes from the environment.
    assert "DOCPLANE_API" in names


def test_module_imports_the_contract():
    imported = {
        alias.name
        for node in ast.walk(_tree()) if isinstance(node, ast.ImportFrom)
        and node.module == "secret_source"
        for alias in node.names
    }
    assert "read_secret" in imported


def test_consumer_fails_closed_with_no_secret_configured(monkeypatch):
    from secret_source import SecretSourceError, read_secret

    monkeypatch.delenv(TOKEN_VAR, raising=False)
    monkeypatch.delenv(TOKEN_VAR + "_FILE", raising=False)
    with pytest.raises(SecretSourceError):
        read_secret(TOKEN_VAR)


def test_consumer_prefers_the_file_over_a_stale_environment_value(tmp_path, monkeypatch):
    """The exact condition this scheduling change creates: a deployed env file that still
    carries a legacy bearer while the runtime file is the intended source. The invariant register is
    file-delivered from its first deployment; this pins that it can never regress."""
    from secret_source import read_secret

    f = tmp_path / "token"
    f.write_text("FROM-RUNTIME-FILE")
    f.chmod(0o600)
    monkeypatch.setenv(TOKEN_VAR + "_FILE", str(f))
    monkeypatch.setenv(TOKEN_VAR, "FROM-LEGACY-ENV")
    assert read_secret(TOKEN_VAR) == "FROM-RUNTIME-FILE"
