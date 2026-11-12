"""Inbox secret files: no ambiguous sources or permissive/symlink files.

Two file contracts are accepted, nothing else:

* owner-only: a regular file with no group/other/special bits (0400 or 0600);
* governed group-read: exactly 0440, owned by root and the dedicated
  ``docplane-secrets`` group (GID 10012). This is the SECRETS-V3 materializer's
  DocPlane opt-in (charliehub-hub2 #1420), which lets the API read the file
  through a supplemental group without the host naming the container UID.

Any other group bit, world access, a different group, or a non-root owner of a
group-readable file is refused.
"""
from __future__ import annotations
import os
import re
import stat
from pathlib import Path


class InboxSecretError(RuntimeError):
    pass


# Fixed by the SECRETS-V3 group-read contract; deliberately not configurable.
GROUP_READ_UID = 0
GROUP_READ_GID = 10012
_OWNER_ONLY_FORBIDDEN = 0o7177


def _mode_allowed(st: os.stat_result) -> bool:
    mode = stat.S_IMODE(st.st_mode)
    if mode == 0o440:
        return st.st_uid == GROUP_READ_UID and st.st_gid == GROUP_READ_GID
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
