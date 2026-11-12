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


def _group_read_identity(monkeypatch, path):
    """Bind the fixed group-read identity to the test file's real owner.

    chown to root:10012 needs privilege CI does not have, so the module's fixed
    identity is pointed at whatever owns the synthetic file instead.
    """
    import app.inbox_secrets as inbox_secrets
    st = os.stat(path)
    monkeypatch.setattr(inbox_secrets, "GROUP_READ_UID", st.st_uid)
    monkeypatch.setattr(inbox_secrets, "GROUP_READ_GID", st.st_gid)
    return inbox_secrets


def test_governed_group_read_file_accepted(tmp_path, monkeypatch):
    p = tmp_path / "cursor"
    p.write_text("x" * 32)
    p.chmod(0o440)
    _group_read_identity(monkeypatch, p)
    monkeypatch.setenv("TEST_INBOX_FILE", str(p))
    assert read_secret("TEST_INBOX") == "x" * 32


@pytest.mark.parametrize("mode", [0o460, 0o640, 0o444, 0o450, 0o040, 0o2440, 0o1440])
def test_other_group_modes_refused_even_with_governed_identity(tmp_path, monkeypatch, mode):
    p = tmp_path / "cursor"
    p.write_text("x" * 32)
    _group_read_identity(monkeypatch, p)
    p.chmod(mode)
    monkeypatch.setenv("TEST_INBOX_FILE", str(p))
    with pytest.raises(InboxSecretError):
        read_secret("TEST_INBOX")


def test_group_read_refused_for_wrong_group_or_owner(tmp_path, monkeypatch):
    p = tmp_path / "cursor"
    p.write_text("x" * 32)
    p.chmod(0o440)
    inbox_secrets = _group_read_identity(monkeypatch, p)
    monkeypatch.setenv("TEST_INBOX_FILE", str(p))
    monkeypatch.setattr(inbox_secrets, "GROUP_READ_GID", os.stat(p).st_gid + 1)
    with pytest.raises(InboxSecretError):
        read_secret("TEST_INBOX")
    _group_read_identity(monkeypatch, p)
    monkeypatch.setattr(inbox_secrets, "GROUP_READ_UID", os.stat(p).st_uid + 1)
    with pytest.raises(InboxSecretError):
        read_secret("TEST_INBOX")


def test_group_read_identity_is_the_secrets_v3_contract():
    import app.inbox_secrets as inbox_secrets
    assert (inbox_secrets.GROUP_READ_UID, inbox_secrets.GROUP_READ_GID) == (0, 10012)


def test_key_directory_accepts_governed_group_read_keys(tmp_path, monkeypatch):
    for name in ("k1", "k2"):
        p = tmp_path / name
        p.write_text(name * 16)
        p.chmod(0o440)
    _group_read_identity(monkeypatch, tmp_path / "k1")
    assert set(read_key_directory(str(tmp_path))) == {"k1", "k2"}


def test_key_directory_ignores_materializer_staging_files(tmp_path):
    key = tmp_path / "k1"
    key.write_text("x" * 32)
    key.chmod(0o400)
    staging = tmp_path / ".chsec-abc123.tmp"
    staging.write_text("partial")
    staging.chmod(0o600)
    assert read_key_directory(str(tmp_path)) == {"k1": "x" * 32}


def test_key_directory_with_only_staging_files_is_empty(tmp_path):
    staging = tmp_path / ".chsec-abc123.tmp"
    staging.write_text("x" * 32)
    staging.chmod(0o400)
    with pytest.raises(InboxSecretError):
        read_key_directory(str(tmp_path))


def test_inbox_secrets_overlay_contract():
    """The opt-in overlay mounts whole governed directories read-only via GID 10012."""
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parents[2]
    overlay = yaml.safe_load((root / "docker-compose.inbox-secrets.yml").read_text())
    api = overlay["services"]["docs-api"]
    assert api["group_add"] == ["10012"]
    env = api["environment"]
    assert set(env) == {"DOCPLANE_WORK_INBOX_CURSOR_SECRET_FILE",
                        "DOCPLANE_WORK_INBOX_ACTIVE_KID_FILE",
                        "DOCPLANE_WORK_INBOX_SIGNING_KEYS_DIR"}
    mounts = {m["target"]: m for m in api["volumes"]}
    assert set(mounts) == {"/run/docplane-inbox/api", "/run/docplane-inbox/signing"}
    for target, mount in mounts.items():
        assert mount["type"] == "bind" and mount["read_only"] is True
        assert mount["bind"] == {"create_host_path": False}
    assert mounts["/run/docplane-inbox/api"]["source"].endswith("/docplane-inbox-api")
    assert mounts["/run/docplane-inbox/signing"]["source"].endswith("/docplane-inbox-signing")
    assert env["DOCPLANE_WORK_INBOX_SIGNING_KEYS_DIR"] == "/run/docplane-inbox/signing"
    for name in ("DOCPLANE_WORK_INBOX_CURSOR_SECRET_FILE", "DOCPLANE_WORK_INBOX_ACTIVE_KID_FILE"):
        assert env[name].startswith("/run/docplane-inbox/api/")
    base = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]["docs-api"]
    assert "group_add" not in base
