"""Contract tests for the SECRETS-V3 `_FILE` secret source.

Every failure mode is asserted against a SENTINEL value so a regression that leaks the secret
into an exception message, a warning, or a repr fails the suite loudly.
"""
import os
import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from secret_source import (  # noqa: E402
    SecretSourceDeprecation,
    SecretSourceError,
    describe_source,
    read_secret,
)

NAME = "DOCPLANE_WORK_CATALOGUE_TOKEN"
FILE_VAR = NAME + "_FILE"
# A value distinctive enough that any leak into a message is unmistakable.
SENTINEL = "SENTINEL-SECRET-VALUE-d0not-l3ak"
LEGACY = "LEGACY-ENV-VALUE-also-must-n0t-leak"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(NAME, raising=False)
    monkeypatch.delenv(FILE_VAR, raising=False)


def _write(tmp_path, content, *, mode=0o600, name="token"):
    p = tmp_path / name
    p.write_text(content)
    p.chmod(mode)
    return p


# ---------------------------------------------------------------------------------------------
# The six contract cases
# ---------------------------------------------------------------------------------------------
def test_file_only(tmp_path, monkeypatch):
    monkeypatch.setenv(FILE_VAR, str(_write(tmp_path, SENTINEL)))
    assert read_secret(NAME) == SENTINEL


def test_env_only_is_accepted_with_a_deprecation_warning(monkeypatch):
    monkeypatch.setenv(NAME, LEGACY)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert read_secret(NAME) == LEGACY
    assert any(issubclass(w.category, SecretSourceDeprecation) for w in caught)
    # The warning must name the variable, never the value.
    for w in caught:
        assert LEGACY not in str(w.message)


def test_both_set_file_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(FILE_VAR, str(_write(tmp_path, SENTINEL)))
    monkeypatch.setenv(NAME, LEGACY)
    assert read_secret(NAME) == SENTINEL


def test_unreadable_file_fails_and_does_not_fall_back(tmp_path, monkeypatch):
    """THE rule that matters: a configured file that cannot be read must never revive the
    legacy plaintext path."""
    p = _write(tmp_path, SENTINEL, mode=0o000)
    if os.geteuid() == 0:
        pytest.skip("running as root: DAC does not deny root, cannot exercise PermissionError")
    monkeypatch.setenv(FILE_VAR, str(p))
    monkeypatch.setenv(NAME, LEGACY)          # present, and must be IGNORED
    with pytest.raises(SecretSourceError) as exc:
        read_secret(NAME)
    assert LEGACY not in str(exc.value)
    assert SENTINEL not in str(exc.value)


def test_missing_file_fails_and_does_not_fall_back(tmp_path, monkeypatch):
    monkeypatch.setenv(FILE_VAR, str(tmp_path / "absent"))
    monkeypatch.setenv(NAME, LEGACY)
    with pytest.raises(SecretSourceError) as exc:
        read_secret(NAME)
    assert LEGACY not in str(exc.value)


def test_empty_file_fails(tmp_path, monkeypatch):
    monkeypatch.setenv(FILE_VAR, str(_write(tmp_path, "   \n")))
    monkeypatch.setenv(NAME, LEGACY)
    with pytest.raises(SecretSourceError) as exc:
        read_secret(NAME)
    assert LEGACY not in str(exc.value)


def test_neither_set_fails(monkeypatch):
    with pytest.raises(SecretSourceError) as exc:
        read_secret(NAME)
    assert FILE_VAR in str(exc.value) and NAME in str(exc.value)


# ---------------------------------------------------------------------------------------------
# Redaction and terminal-state enforcement
# ---------------------------------------------------------------------------------------------
def test_no_failure_mode_leaks_the_value(tmp_path, monkeypatch):
    """Sweep every failure path and assert the sentinel never appears in the message."""
    cases = []
    monkeypatch.setenv(FILE_VAR, str(tmp_path / "nope"))
    monkeypatch.setenv(NAME, SENTINEL)
    cases.append("missing")
    for _ in cases:
        with pytest.raises(SecretSourceError) as exc:
            read_secret(NAME)
        assert SENTINEL not in str(exc.value)
        assert SENTINEL not in repr(exc.value)

    monkeypatch.setenv(FILE_VAR, str(_write(tmp_path, "")))
    with pytest.raises(SecretSourceError) as exc:
        read_secret(NAME)
    assert SENTINEL not in str(exc.value)

    monkeypatch.delenv(FILE_VAR)
    monkeypatch.setenv(NAME, SENTINEL)
    with pytest.raises(SecretSourceError) as exc:
        read_secret(NAME, allow_env_fallback=False)
    assert SENTINEL not in str(exc.value)


def test_non_utf8_file_fails(tmp_path, monkeypatch):
    p = tmp_path / "bin"
    p.write_bytes(b"\xff\xfe\x00\x01")
    p.chmod(0o600)
    monkeypatch.setenv(FILE_VAR, str(p))
    with pytest.raises(SecretSourceError):
        read_secret(NAME)


def test_terminal_state_refuses_env(monkeypatch):
    monkeypatch.setenv(NAME, LEGACY)
    with pytest.raises(SecretSourceError) as exc:
        read_secret(NAME, allow_env_fallback=False)
    assert "TERMINAL" in str(exc.value)
    assert LEGACY not in str(exc.value)


def test_trailing_newline_is_stripped(tmp_path, monkeypatch):
    """The materializer writes byte-exact values, but an operator-authored file may end with a
    newline. Stripping is required or the bearer would be sent with a trailing \\n."""
    monkeypatch.setenv(FILE_VAR, str(_write(tmp_path, SENTINEL + "\n")))
    assert read_secret(NAME) == SENTINEL


# ---------------------------------------------------------------------------------------------
# describe_source is diagnostics-only and value-blind
# ---------------------------------------------------------------------------------------------
def test_describe_source_never_returns_the_value(tmp_path, monkeypatch):
    monkeypatch.setenv(FILE_VAR, str(_write(tmp_path, SENTINEL)))
    d = describe_source(NAME)
    assert d["source"] == "file" and d["present"] is True
    assert SENTINEL not in repr(d)

    monkeypatch.delenv(FILE_VAR)
    monkeypatch.setenv(NAME, SENTINEL)
    d = describe_source(NAME)
    assert d["source"] == "environment" and d["deprecated"] is True
    assert SENTINEL not in repr(d)


def test_describe_source_reports_absence(monkeypatch):
    assert describe_source(NAME)["source"] is None
