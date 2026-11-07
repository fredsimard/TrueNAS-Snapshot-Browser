"""Who a signed-in account is on the host: uid and groups (PLAN.md §8).

Read from the host's /etc/passwd and /etc/group, mounted read-only into the container. They're the
same accounts Samba uses for local users, so the kernel checks the same identity for the app as for
SMB.

TrueNAS replaces both files whenever an account or group changes, and a bind mount of a single file
keeps showing the file it was mounted with. The files are held open from startup, so replaced() can
tell when that happened: the app then exits, and the container restarts with the new files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .config import ConfigError

HOST_PASSWD = "/host/etc/passwd"
HOST_GROUP = "/host/etc/group"


@dataclass(frozen=True)
class Identity:
    name: str
    uid: int
    gid: int
    groups: tuple[int, ...]  # supplementary groups, including gid


def _rows(text: str, fields: int) -> list[list[str]]:
    out = []
    for line in text.splitlines():
        p = line.split(":")
        if len(p) >= fields and not line.startswith(("#", "+", "-")):
            out.append(p)
    return out


def _id(s: str) -> int | None:
    return int(s) if s.isascii() and s.isdigit() else None


def parse_identity(username: str, passwd: str, group: str) -> Identity | None:
    """The account matching username, or None if there's none, it's ambiguous, or it's root's.

    SMB usernames aren't case-sensitive, so an exact match wins, then a single case-insensitive
    one. Root (uid or primary group 0) is never returned; group 0 is dropped from the rest.
    """
    users = [p for p in _rows(passwd, 7) if _id(p[2]) is not None and _id(p[3]) is not None]
    exact = [p for p in users if p[0] == username]
    if not exact:
        want = username.casefold()
        exact = [p for p in users if p[0].casefold() == want]
    if len(exact) != 1:
        return None
    name, uid, gid = exact[0][0], _id(exact[0][2]), _id(exact[0][3])
    if uid == 0 or gid == 0:
        return None
    groups = {gid}
    for p in _rows(group, 4):
        g = _id(p[2])
        if g and name in p[3].split(","):
            groups.add(g)
    return Identity(name=name, uid=uid, gid=gid, groups=tuple(sorted(groups)))


class HostAccounts:
    def __init__(self, passwd_path: str = HOST_PASSWD, group_path: str = HOST_GROUP):
        self._fds: list[int] = []
        try:
            for path in (passwd_path, group_path):
                self._fds.append(os.open(path, os.O_RDONLY | os.O_CLOEXEC))
        except OSError as e:
            self.close()
            raise ConfigError(
                f"{e.filename}: {e.strerror}. Mount the host's /etc/passwd and /etc/group read-only at "
                f"{HOST_PASSWD} and {HOST_GROUP}."
            ) from None

    def _read(self, fd: int) -> str:
        chunks, offset = [], 0
        while chunk := os.pread(fd, 1 << 16, offset):
            chunks.append(chunk)
            offset += len(chunk)
        return b"".join(chunks).decode("utf-8", "replace")

    def lookup(self, username: str) -> Identity | None:
        return parse_identity(username, self._read(self._fds[0]), self._read(self._fds[1]))

    def replaced(self) -> bool:
        """True once the host has replaced either file: what's mounted here is then out of date."""
        return any(os.fstat(fd).st_nlink == 0 for fd in self._fds)

    def close(self) -> None:
        for fd in self._fds:
            os.close(fd)
        self._fds = []
