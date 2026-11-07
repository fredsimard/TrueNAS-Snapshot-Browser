"""Downloads (PLAN.md §9), worker side: one file as is, or a zip made while it's sent.

Everything here runs in a worker, as the signed-in account, so a folder that account can't open is
left out of a zip exactly as it's left out of a listing. Paths are walked with O_NOFOLLOW from the
share root, like everything else, and symlinks and hidden entries are never included.

The web process gets an open file descriptor, never a path: the file itself for a single file, the
read end of a pipe for a zip. A thread in the worker writes the zip into the pipe as the web process
reads from it, so nothing is staged on disk and memory stays flat whatever the size. Entries are
stored, not compressed: most of what's on these shares is already compressed, and it keeps the
throughput at disk speed.
"""

from __future__ import annotations

import os
import shutil
import stat
import threading
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .paths import Denied, Missing, _translate, _visible, opened_dir

MAX_ZIP_ENTRIES = 100_000
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_NOCTTY", 0)
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def download_name(name: str, when: datetime) -> str:
    """Logo_final.psd from a version of Sept 12, 2 PM → Logo_final (2026-09-12 14.00).psd"""
    stamp = when.strftime("%Y-%m-%d %H.%M")
    stem, dot, ext = name.rpartition(".")
    if not stem:
        return f"{name} ({stamp})"
    return f"{stem} ({stamp}){dot}{ext}"


def local_time(mtime_ns: int, tz: ZoneInfo | None) -> datetime:
    t = datetime.fromtimestamp(mtime_ns / 1e9, timezone.utc)
    return t.astimezone(tz) if tz is not None else t.astimezone()


class TooLarge(Exception):
    pass


@dataclass(frozen=True)
class Item:
    arc: tuple[str, ...]  # path inside the zip, relative to the folder downloaded from
    is_dir: bool
    size: int
    mtime_ns: int


@dataclass
class ZipPlan:
    items: list[Item] = field(default_factory=list)
    bytes: int = 0
    files: int = 0
    folders: int = 0


def plan_zip(base: str, folder: Sequence[str], names: Sequence[str], max_bytes: int) -> ZipPlan:
    """Every folder and regular file under folder/names that this process can read.

    Raises TooLarge as soon as the contents pass max_bytes or MAX_ZIP_ENTRIES, so a request for a
    whole share stops early instead of walking it. Raises Missing or Denied for the folder itself.
    """
    plan = ZipPlan()

    def add(item: Item) -> None:
        plan.items.append(item)
        if item.is_dir:
            plan.folders += 1
        else:
            plan.files += 1
            plan.bytes += item.size
        if plan.bytes > max_bytes or len(plan.items) > MAX_ZIP_ENTRIES:
            raise TooLarge()

    def walk(dfd: int, arc: tuple[str, ...]) -> None:
        try:
            with os.scandir(dfd) as it:
                entries = sorted((de for de in it if _visible(de.name)), key=lambda de: de.name)
        except OSError:
            return
        for de in entries:
            consider(dfd, de.name, arc)

    def consider(dfd: int, name: str, arc: tuple[str, ...]) -> None:
        try:
            st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        except OSError:
            return
        here = arc + (name,)
        if stat.S_ISREG(st.st_mode):
            add(Item(here, False, st.st_size, st.st_mtime_ns))
        elif stat.S_ISDIR(st.st_mode):
            try:
                cfd = os.open(name, _DIR_FLAGS, dir_fd=dfd)
            except OSError:
                return  # can't open it: left out, as in a listing
            try:
                add(Item(here, True, 0, st.st_mtime_ns))
                walk(cfd, here)
            finally:
                os.close(cfd)

    with opened_dir(base, folder) as fd:
        for name in dict.fromkeys(names):
            consider(fd, name, ())
    return plan


def open_file(base: str, parts: Sequence[str]) -> tuple[int, os.stat_result]:
    """An open descriptor for the regular file at base/parts. Raises Missing or Denied."""
    with opened_dir(base, parts[:-1]) as dfd:
        try:
            fd = os.open(parts[-1], _FILE_FLAGS, dir_fd=dfd)
        except OSError as e:
            raise _translate(e) from None
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise Missing()
    return fd, st


def start_zip(base: str, folder: Sequence[str], plan: ZipPlan, tz: ZoneInfo | None) -> int:
    """The read end of a pipe that a thread fills with a zip of plan. A zip that couldn't be finished
    ends without its central directory, so whoever reads it can tell."""
    r, w = os.pipe()
    os.set_inheritable(r, False)
    os.set_inheritable(w, False)
    threading.Thread(target=_write_zip, args=(w, base, tuple(folder), plan, tz), daemon=True).start()
    return r


def _write_zip(w: int, base: str, folder: tuple[str, ...], plan: ZipPlan, tz: ZoneInfo | None) -> None:
    out = os.fdopen(w, "wb", buffering=1 << 20)
    zf = None
    try:
        zf = zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED, allowZip64=True)
        for item in plan.items:
            info = zipfile.ZipInfo("/".join(item.arc) + ("/" if item.is_dir else ""), _zip_time(item.mtime_ns, tz))
            if item.is_dir:
                info.external_attr = (stat.S_IFDIR | 0o755) << 16 | 0x10
                zf.writestr(info, b"")
                continue
            try:
                fd, st = open_file(base, folder + item.arc)
            except (Missing, Denied):
                continue  # gone or locked since it was listed
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.file_size = st.st_size
            with os.fdopen(fd, "rb") as src, zf.open(info, "w", force_zip64=st.st_size > 1 << 30) as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
        zf.close()  # writes the central directory: the zip is complete only now
        out.close()
    except BaseException:
        # Broken pipe (the download was cancelled) or an unreadable file: stop without finishing.
        # ZipFile would write its central directory when garbage-collected, making it look whole.
        if zf is not None:
            zf.fp = None
        try:
            out.close()
        except Exception:
            pass


def _zip_time(mtime_ns: int, tz: ZoneInfo | None) -> tuple[int, int, int, int, int, int]:
    t = local_time(mtime_ns, tz)
    if t.year < 1980:
        return _ZIP_EPOCH
    if t.year > 2107:
        return (2107, 12, 31, 23, 59, 58)
    return (t.year, t.month, t.day, t.hour, t.minute, t.second)


ZIP_END_SIGNATURE = b"PK\x05\x06"
ZIP_END_SIZE = 22  # end of central directory record, with no comment


def zip_complete(tail: bytes) -> bool:
    """True if a zip's last bytes are a finished end-of-central-directory record."""
    return len(tail) >= ZIP_END_SIZE and tail[-ZIP_END_SIZE:].startswith(ZIP_END_SIGNATURE)
