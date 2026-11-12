"""Inbox secret files: no ambiguous sources or permissive/symlink files.

Two file contracts are accepted, nothing else:

* owner-only: a regular file with no group/other/special bits (0400 or 0600);
* group-read: exactly 0440, owned by root, and owned by a group this process
  holds. The deployment grants that group (for example a Compose ``group_add``
  of the host secret store's group), so DocPlane carries no host GID of its
  own. This matches a governed store such as CharlieHub SECRETS-V3's
  group-read opt-in, which lets the API read the file through a supplemental
  group without the host naming the container UID.

Any other group bit, world access, a group this process does not hold, or a
non-root owner of a group-readable file is refused. Root ownership matters
because a file's owner can widen its mode; only root may own a shared secret.
"""
from __future__ import annotations
import os
import re
import stat
from pathlib import Path


class InboxSecretError(RuntimeError):
    pass


# Root (uid 0) is the only owner allowed for a group-readable secret.
GROUP_READ_OWNER_UID = 0
_OWNER_ONLY_FORBIDDEN = 0o7177


def _held_groups() -> set[int]:
    """Groups this process actually holds: its effective group and supplementary groups."""
    return set(os.getgroups()) | {os.getegid()}


def _mode_allowed(st: os.stat_result) -> bool:
    mode = stat.S_IMODE(st.st_mode)
    if mode == 0o440:
        return st.st_uid == GROUP_READ_OWNER_UID and st.st_gid in _held_groups()
    return not mode & _OWNER_ONLY_FORBIDDEN and bool(mode & 0o400)


def read_file(path: str) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or not _mode_allowed(st):
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
    # The materializer stages each replacement as a hidden ".chsec-*.tmp" file in
    # this directory before an atomic rename, and may leave one behind if it is
    # interrupted. A hidden name is never a valid key ID, so skip it rather than
    # failing every request while a key is being written.
    entries = [entry for entry in entries if not entry.name.startswith(".")]
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
