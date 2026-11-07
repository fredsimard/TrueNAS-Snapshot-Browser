"""Path model (PLAN.md §5) and the only code that turns user input into filesystem access.

    share_root          configured; the user-visible root
    dataset_mountpoint  nearest ancestor of share_root with an accessible .zfs/snapshot
    relative_prefix     share_root relative to dataset_mountpoint; internal only

    live:          share_root / rel
    snapshot X:    dataset_mountpoint / .zfs/snapshot / X / relative_prefix / rel

`rel` arrives from the client and is validated by parse_rel(). open_dir() then walks it one
component at a time with O_NOFOLLOW, so a symlink anywhere in the path is never followed.
"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from .config import ConfigError, RootConfig

MAX_REL_BYTES = 4096
MAX_NAME_BYTES = 255

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
_MISSING_ERRNOS = {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}
_DENIED_ERRNOS = {errno.EACCES, errno.EPERM}


class PathRejected(ValueError):
    pass


class Missing(Exception):
    """The folder does not exist at this point in time (or a component is a file or symlink)."""


class Denied(Exception):
    """The kernel refused access."""


def parse_rel(rel: str) -> tuple[str, ...]:
    """Validate a client-supplied path relative to the share root.

    Empty segments are dropped. Any segment starting with "." is rejected: that covers ".", "..",
    ".zfs" (which would bypass the snapshot prefix filter at a dataset root) and hidden entries,
    which listings never show.
    """
    if "\x00" in rel or len(rel.encode("utf-8", "surrogatepass")) > MAX_REL_BYTES:
        raise PathRejected("invalid path")
    parts = tuple(p for p in rel.split("/") if p)
    for p in parts:
        if p.startswith(".") or len(p.encode("utf-8", "surrogatepass")) > MAX_NAME_BYTES:
            raise PathRejected("invalid path")
        if not is_valid_name(p):
            raise PathRejected("invalid path")
    return parts


def is_valid_name(name: str) -> bool:
    """True for names that round-trip through UTF-8 (listings skip the rest)."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


@dataclass(frozen=True)
class Root:
    id: str
    label: str
    share_root: str
    dataset_mountpoint: str
    relative_prefix: tuple[str, ...]

    @property
    def snapshot_dir(self) -> str:
        return os.path.join(self.dataset_mountpoint, ".zfs", "snapshot")

    def base(self, snapshot_name: str | None) -> str:
        """Directory corresponding to the share root, live (None) or inside a snapshot."""
        if snapshot_name is None:
            return self.share_root
        return os.path.join(self.snapshot_dir, snapshot_name, *self.relative_prefix)


def has_snapshot_dir(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".zfs", "snapshot"))


def detect_dataset_mountpoint(path: str) -> str | None:
    p = path
    while True:
        if has_snapshot_dir(p):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def resolve_root(cfg: RootConfig) -> Root:
    where = f"SHARES: {cfg.label}"
    share = os.path.realpath(cfg.share_root)
    if not os.path.isdir(share):
        raise ConfigError(
            f"{where}: {cfg.share_root} isn't in the container. Mount the dataset that holds it, read-only, "
            "at the same path."
        )
    if cfg.dataset_mountpoint:
        dataset = os.path.realpath(cfg.dataset_mountpoint)
        if os.path.commonpath([share, dataset]) != dataset:
            raise ConfigError(f"{where}: dataset_mountpoint must be the share's folder or one of its parents")
        if not has_snapshot_dir(dataset):
            raise ConfigError(f"{where}: {dataset}/.zfs/snapshot is not accessible")
    else:
        found = detect_dataset_mountpoint(share)
        if found is None:
            raise ConfigError(
                f"{where}: no .zfs/snapshot at {share} or any parent. Mount the dataset that holds the share "
                "(its mountpoint, not only the share's folder) into the container."
            )
        dataset = found
    rel = os.path.relpath(share, dataset)
    prefix = () if rel == "." else tuple(rel.split(os.sep))
    return Root(
        id=cfg.id,
        label=cfg.label,
        share_root=share,
        dataset_mountpoint=dataset,
        relative_prefix=prefix,
    )


def _translate(e: OSError) -> Exception:
    if e.errno in _MISSING_ERRNOS:
        return Missing()
    if e.errno in _DENIED_ERRNOS:
        return Denied()
    return e


@contextmanager
def opened_dir(base: str, parts: Sequence[str]) -> Iterator[int]:
    """Open base (trusted) then each of parts (untrusted) without following symlinks."""
    try:
        fd = os.open(base, _DIR_FLAGS)
    except OSError as e:
        raise _translate(e) from None
    try:
        for part in parts:
            try:
                nfd = os.open(part, _DIR_FLAGS | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as e:
                raise _translate(e) from None
            os.close(fd)
            fd = nfd
        yield fd
    finally:
        os.close(fd)


@dataclass(frozen=True)
class Entry:
    name: str
    is_dir: bool
    size: int | None  # None for folders
    mtime_ns: int
    ino: int
    ctime_ns: int

    @classmethod
    def from_stat(cls, name: str, st: os.stat_result) -> Entry | None:
        if stat.S_ISDIR(st.st_mode):
            is_dir = True
        elif stat.S_ISREG(st.st_mode):
            is_dir = False
        else:
            return None  # symlinks, devices, sockets: not shown
        return cls(
            name=name,
            is_dir=is_dir,
            size=None if is_dir else st.st_size,
            mtime_ns=st.st_mtime_ns,
            ino=st.st_ino,
            ctime_ns=st.st_ctime_ns,
        )

    @property
    def content(self) -> tuple:
        """What users compare: kind, size and modification time."""
        return (self.is_dir, self.size, self.mtime_ns)

    @property
    def identity(self) -> tuple:
        """Same inode, unchanged since. ZFS keeps inode numbers and ctime across snapshots, and any
        modification or rename bumps ctime, so equal identities at two points in time mean the file
        was untouched in between."""
        return (self.ino, self.ctime_ns) + self.content


def _visible(name: str) -> bool:
    return not name.startswith(".") and is_valid_name(name)


def read_dir(base: str, parts: Sequence[str]) -> dict[str, Entry]:
    """Visible folders and regular files at base/parts. Raises Missing or Denied."""
    with opened_dir(base, parts) as fd:
        out: dict[str, Entry] = {}
        try:
            with os.scandir(fd) as it:
                for de in it:
                    if not _visible(de.name):
                        continue
                    try:
                        st = de.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue  # removed while listing (live tree)
                    entry = Entry.from_stat(de.name, st)
                    if entry is not None:
                        out[de.name] = entry
        except OSError as e:
            raise _translate(e) from None
        return out


def list_names(base: str, parts: Sequence[str]) -> set[str]:
    """Visible names at base/parts, without stat. Raises Missing or Denied."""
    with opened_dir(base, parts) as fd:
        try:
            return {n for n in os.listdir(fd) if _visible(n)}
        except OSError as e:
            raise _translate(e) from None


def folder_exists(base: str, parts: Sequence[str]) -> bool:
    try:
        with opened_dir(base, parts):
            return True
    except Missing:
        return False
    except Denied:
        return True


def observe_file(base: str, parts: Sequence[str]) -> tuple[Entry | None, tuple]:
    """(entry, key) for the regular file at base/parts.

    The key is compared between points in time: equal keys at both ends of a range mean nothing
    changed in between. When the file is absent, the key is the parent folder's identity, since
    creating or deleting a name inside a folder changes the folder's ctime.
    """
    parent, name = parts[:-1], parts[-1]
    try:
        with opened_dir(base, parent) as fd:
            try:
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except OSError as e:
                err = _translate(e)
                if isinstance(err, Missing):
                    st = None
                elif isinstance(err, Denied):
                    return None, ("denied",)
                else:
                    raise err from None
            entry = Entry.from_stat(name, st) if st is not None else None
            if entry is not None and not entry.is_dir:
                return entry, entry.identity
            pst = os.fstat(fd)
            return None, ("absent", pst.st_ino, pst.st_ctime_ns)
    except Missing:
        return None, _missing_folder_key(base, parent)
    except Denied:
        return None, ("denied",)


def _missing_folder_key(base: str, parent: Sequence[str]) -> tuple:
    """Key for a file whose parent folder doesn't exist: the identity of the closest folder above it
    that does. Creating or removing the missing folder changes that folder's ctime, so two points
    that both lack it compare equal only if it didn't come and go in between."""
    for depth in range(len(parent) - 1, -1, -1):
        try:
            with opened_dir(base, parent[:depth]) as fd:
                st = os.fstat(fd)
                return ("absent", st.st_ino, st.st_ctime_ns)
        except Missing:
            continue
        except Denied:
            return ("denied",)
    return ("absent",)
