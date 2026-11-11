"""Inbox secret files: no ambiguous sources or permissive/symlink files."""
from __future__ import annotations
import os
import re
import stat
from pathlib import Path


class InboxSecretError(RuntimeError):
    pass


def read_file(path: str) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o177 or not st.st_mode & 0o400:
                raise InboxSecretError("invalid secret file mode or type")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(65537)
            if not raw or len(raw) > 65536:
                raise InboxSecretError("invalid secret file size")
            value = raw.decode("utf-8").strip()
            if not value:
                raise InboxSecretError("empty secret file")
            return value
        finally:
            os.close(fd)
    except (OSError, UnicodeError) as exc:
        raise InboxSecretError("secret file unavailable") from exc


def read_secret(name: str) -> str:
    path = os.environ.get(name + "_FILE", "")
    value = os.environ.get(name, "")
    if path and name in os.environ:
        raise InboxSecretError("ambiguous secret sources")
    return read_file(path) if path else value


def read_key_directory(path: str) -> dict[str, str]:
    try:
        entries = list(Path(path).iterdir())
    except OSError as exc:
        raise InboxSecretError("key directory unavailable") from exc
    if not entries or len(entries) > 128:
        raise InboxSecretError("invalid key directory")
    keys = {}
    for entry in entries:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", entry.name):
            raise InboxSecretError("invalid key id")
        value = read_file(str(entry))
        if len(value.encode()) < 32:
            raise InboxSecretError("invalid signing key")
        keys[entry.name] = value
    return keys
