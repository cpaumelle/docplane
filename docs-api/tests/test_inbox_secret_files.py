import os
import pytest
from app.inbox_secrets import InboxSecretError, read_secret, read_key_directory


def test_file_modes_conflicts_and_missing(tmp_path, monkeypatch):
    p = tmp_path / "secret"
    p.write_text("synthetic-secret")
    p.chmod(0o600)
    monkeypatch.setenv("TEST_INBOX_FILE", str(p))
    assert read_secret("TEST_INBOX") == "synthetic-secret"
    monkeypatch.setenv("TEST_INBOX", "")
    with pytest.raises(InboxSecretError): read_secret("TEST_INBOX")
    monkeypatch.delenv("TEST_INBOX")
    p.chmod(0o640)
    with pytest.raises(InboxSecretError): read_secret("TEST_INBOX")
    p.unlink()
    with pytest.raises(InboxSecretError): read_secret("TEST_INBOX")
    target = tmp_path / "target"
    target.write_text("synthetic-secret")
    target.chmod(0o400)
    p.symlink_to(target)
    with pytest.raises(InboxSecretError): read_secret("TEST_INBOX")


def test_key_directory_retains_old_keys_and_rejects_unsafe(tmp_path):
    for name in ("k1", "k2"):
        p = tmp_path / name
        p.write_text("x" * 32)
        p.chmod(0o400)
    assert set(read_key_directory(str(tmp_path))) == {"k1", "k2"}
    (tmp_path / "k2").chmod(0o644)
    with pytest.raises(InboxSecretError): read_key_directory(str(tmp_path))


def test_startup_rejects_ambiguous_or_bad_file(monkeypatch, tmp_path):
    from app.application import validate_inbox_secret_configuration
    name = "DOCPLANE_WORK_INBOX_CURSOR_SECRET"
    p = tmp_path / "cursor"
    p.write_text("x" * 32)
    p.chmod(0o600)
    monkeypatch.setenv(name + "_FILE", str(p))
    monkeypatch.setenv(name, "another")
    with pytest.raises(InboxSecretError): validate_inbox_secret_configuration()
    monkeypatch.delenv(name)
    p.chmod(0o644)
    with pytest.raises(InboxSecretError): validate_inbox_secret_configuration()
