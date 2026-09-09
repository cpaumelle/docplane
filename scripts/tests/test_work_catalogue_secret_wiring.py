"""CANARY-1 consumer wiring: work_catalogue.py must resolve its bearer through the SECRETS-V3
`_FILE` contract, not through the plaintext environment.

These are deliberately INERT: they never contact DocPlane and never need the real credential.
"""
import ast
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

TARGET = SCRIPTS / "work_catalogue.py"
TOKEN_VAR = "DOCPLANE_WORK_CATALOGUE_TOKEN"


def _tree():
    return ast.parse(TARGET.read_text())


def _calls(tree):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def test_token_is_read_through_read_secret():
    """The bearer must be resolved by read_secret(...), never by _required_environment(...)."""
    calls = _calls(_tree())
    read_secret_args = [
        n.args[0].value for n in calls
        if isinstance(n.func, ast.Name) and n.func.id == "read_secret"
        and n.args and isinstance(n.args[0], ast.Constant)
    ]
    assert TOKEN_VAR in read_secret_args, (
        "the catalogue bearer is not resolved through read_secret()")


def test_token_is_not_read_from_the_plaintext_environment():
    """Guard against a regression that reintroduces direct environment delivery."""
    calls = _calls(_tree())
    env_args = [
        n.args[0].value for n in calls
        if isinstance(n.func, ast.Name) and n.func.id == "_required_environment"
        and n.args and isinstance(n.args[0], ast.Constant)
    ]
    assert TOKEN_VAR not in env_args, (
        f"{TOKEN_VAR} is still read via _required_environment(); SECRETS-V3 requires the "
        f"`_FILE` contract for governed secrets")
    # Non-secret configuration legitimately still comes from the environment.
    assert "DOCPLANE_API" in env_args


def test_module_imports_the_contract():
    tree = _tree()
    imported = {
        alias.name
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        and node.module == "secret_source"
        for alias in node.names
    }
    assert "read_secret" in imported


def test_consumer_fails_closed_with_no_secret_configured(monkeypatch):
    """With neither variable set the consumer must fail, not fall back to another principal."""
    from secret_source import SecretSourceError, read_secret

    monkeypatch.delenv(TOKEN_VAR, raising=False)
    monkeypatch.delenv(TOKEN_VAR + "_FILE", raising=False)
    with pytest.raises(SecretSourceError):
        read_secret(TOKEN_VAR)


def test_consumer_prefers_the_file_over_a_stale_environment_value(tmp_path, monkeypatch):
    """The exact CANARY-1 migration condition: both delivery paths present at once."""
    from secret_source import read_secret

    f = tmp_path / "token"
    f.write_text("FROM-RUNTIME-FILE")
    f.chmod(0o600)
    monkeypatch.setenv(TOKEN_VAR + "_FILE", str(f))
    monkeypatch.setenv(TOKEN_VAR, "FROM-LEGACY-ENV")
    assert read_secret(TOKEN_VAR) == "FROM-RUNTIME-FILE"
