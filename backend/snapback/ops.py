"""Everything that reads a share, as requests a worker answers (PLAN.md §8).

A worker runs with the signed-in user's identity, so the kernel applies the share's permissions to
every listing and stat here. Requests and answers are plain JSON values, so they can cross a socket;
answers are already in the API's response shape.

    {"op": "points",  "root": id}
    {"op": "folder",  "root": id, "path": rel, "at": point id}
    {"op": "history", "root": id, "path": rel}
    {"op": "plan",    "root": id, "path": folder, "at": point id, "names": [name, …]}
    {"op": "file",    "root": id, "path": rel, "at": point id}
    {"op": "zip",     "root": id, "path": folder, "at": point id, "names": [name, …]}

    → {"ok": value} or {"error": code}

"file" and "zip" also return an open file descriptor as "fd" (passed over the socket by the
worker), and an "audit" value with the absolute path and snapshot name for the server's log. Neither
may reach a client.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

from .browse import FolderResult, History, Row, Timeline, file_history, list_folder
from .config import DEFAULT_MAX_DOWNLOAD_GB, Config
from .downloads import TooLarge, download_name, local_time, open_file, plan_zip, start_zip
from .paths import Denied, Entry, Missing, PathRejected, Root, parse_rel, resolve_root
from .snapshots import SnapshotIndex

log = logging.getLogger(__name__)

# HTTP status for each error code an answer can carry.
ERROR_STATUS = {
    "bad_request": 400,
    "bad_path": 400,
    "denied": 403,
    "not_found": 404,
    "unknown_root": 404,
    "unknown_point": 404,
    "too_large": 413,
}
MAX_NAMES = 10_000


class OpError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Ops:
    def __init__(
        self,
        roots: Mapping[str, Root],
        index: SnapshotIndex,
        tz: ZoneInfo | None = None,
        max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_GB * 10**9,
    ):
        self.roots = dict(roots)
        self.index = index
        self.tz = tz
        self.max_download_bytes = max_download_bytes

    @classmethod
    def from_config(cls, config: Config) -> Ops:
        """Resolve every root. Needs access to each dataset, so the supervisor calls it as root."""
        roots = {rc.id: resolve_root(rc) for rc in config.roots}
        index = SnapshotIndex(
            prefixes=config.snapshot_prefixes,
            max_listed=config.max_snapshots_listed,
            ttl_seconds=config.snapshot_cache_seconds,
            tz=config.timezone,
        )
        for r in roots.values():
            log.info(
                "root %s: share_root=%s dataset_mountpoint=%s relative_prefix=%s snapshots=%d",
                r.id, r.share_root, r.dataset_mountpoint, "/".join(r.relative_prefix) or ".",
                len(index.list(r.snapshot_dir)),
            )
        return cls(roots, index, config.timezone, config.max_download_bytes)

    def handle(self, req: Mapping) -> dict:
        try:
            result = self._run(req)
            if isinstance(result, _WithFd):
                return {"ok": result.value, "fd": result.fd, "audit": result.audit}
            return {"ok": result}
        except OpError as e:
            return {"error": e.code}
        except Exception:
            log.exception("request %s failed", req.get("op"))
            return {"error": "internal"}

    def _run(self, req: Mapping) -> dict | _WithFd:
        root = self.roots.get(req.get("root"))
        if root is None:
            raise OpError("unknown_root")
        tl = Timeline(root, self.index.list(root.snapshot_dir))
        op = req.get("op")
        if op == "points":
            return {"points": [{"id": s.id, "time": s.time.isoformat()} for s in tl.snapshots]}
        parts = _parts(req.get("path"))
        if op == "history":
            if not parts:
                raise OpError("bad_path")
            return history_json(tl, file_history(tl, parts), self.tz)
        at = tl.index_of(str(req.get("at", "now")))
        if at is None:
            raise OpError("unknown_point")
        if op == "folder":
            return folder_json(tl, list_folder(tl, parts, at))
        if op == "file":
            if not parts:
                raise OpError("bad_path")
            return self._file(tl, parts, at)
        if op in ("plan", "zip"):
            return self._zip(tl, parts, at, _names(req.get("names")), start=op == "zip")
        raise OpError("bad_request")

    def _point_time(self, tl: Timeline, at: int) -> datetime:
        return datetime.now(self.tz).astimezone(self.tz) if at == tl.now else tl.snapshots[at].time

    def _audit(self, tl: Timeline, at: int, parts: tuple[str, ...], names: tuple[str, ...] = ()) -> dict:
        audit = {
            "path": os.path.join(tl.root.share_root, *parts),
            "version": "now" if at == tl.now else tl.snapshots[at].name,
        }
        if names:
            more = f" and {len(names) - 10} more" if len(names) > 10 else ""
            audit["items"] = ", ".join(names[:10]) + more
        return audit

    def _file(self, tl: Timeline, parts: tuple[str, ...], at: int) -> _WithFd:
        try:
            fd, st = open_file(tl.base(at), parts)
        except Missing:
            raise OpError("not_found") from None
        except Denied:
            raise OpError("denied") from None
        name = download_name(parts[-1], local_time(st.st_mtime_ns, self.tz))
        return _WithFd({"name": name, "bytes": st.st_size}, fd, self._audit(tl, at, parts))

    def _zip(self, tl: Timeline, folder: tuple[str, ...], at: int, names: tuple[str, ...], start: bool):
        base = tl.base(at)
        try:
            plan = plan_zip(base, folder, names, self.max_download_bytes)
        except TooLarge:
            if start:
                raise OpError("too_large") from None
            return {"kind": "zip", "too_large": True, "limit": self.max_download_bytes}
        except Missing:
            raise OpError("not_found") from None
        except Denied:
            raise OpError("denied") from None
        if not plan.items:
            raise OpError("not_found")
        top = [i for i in plan.items if len(i.arc) == 1]
        if not start and len(top) == 1 and not top[0].is_dir:
            only = top[0]
            name = download_name(only.arc[0], local_time(only.mtime_ns, self.tz))
            return {"kind": "file", "name": name, "bytes": only.size, "files": 1, "folders": 0,
                    "too_large": False, "limit": self.max_download_bytes}
        label = top[0].arc[0] if len(top) == 1 else (folder[-1] if folder else tl.root.label)
        name = download_name(f"{label}.zip", self._point_time(tl, at))
        summary = {"kind": "zip", "name": name, "bytes": plan.bytes, "files": plan.files,
                   "folders": plan.folders, "too_large": False, "limit": self.max_download_bytes}
        if not start:
            return summary
        return _WithFd(summary, start_zip(base, folder, plan, self.tz), self._audit(tl, at, folder, names))


class _WithFd:
    def __init__(self, value: dict, fd: int, audit: dict):
        self.value, self.fd, self.audit = value, fd, audit


def _names(names: object) -> tuple[str, ...]:
    """Names of items inside one folder, as a client sends them for a download."""
    if not isinstance(names, list) or not 0 < len(names) <= MAX_NAMES:
        raise OpError("bad_request")
    out = []
    for n in names:
        if not isinstance(n, str) or _parts(n) != (n,):
            raise OpError("bad_path")
        out.append(n)
    return tuple(out)


def _parts(path: object) -> tuple[str, ...]:
    if not isinstance(path, str):
        raise OpError("bad_path")
    try:
        return parse_rel(path)
    except PathRejected:
        raise OpError("bad_path") from None


def entry_json(e: Entry) -> dict:
    return {"dir": e.is_dir, "size": e.size, "mtime": e.mtime_ns // 1_000_000}


def row_json(tl: Timeline, r: Row) -> dict:
    return {
        "name": r.entry.name,
        **entry_json(r.entry),
        "status": r.status,
        "now": entry_json(r.now) if r.now is not None else None,
        "last_seen": tl.point_id(r.last_seen) if r.last_seen is not None else None,
    }


def folder_json(tl: Timeline, res: FolderResult) -> dict:
    return {
        "state": res.state,
        "entries": [row_json(tl, r) for r in res.rows],
        "nearest": (
            {"at": tl.point_id(res.nearest), "direction": res.direction}
            if res.nearest is not None
            else None
        ),
    }


def history_json(tl: Timeline, h: History, tz: ZoneInfo | None = None) -> dict:
    return {
        "now": entry_json(h.now) if h.now is not None else None,
        "versions": [
            {
                **entry_json(v.entry),
                "current": v.current,
                "points": [tl.point_id(i) for i in v.points],
                "saves_as": download_name(v.entry.name, local_time(v.entry.mtime_ns, tz)),
            }
            for v in h.versions
        ],
    }
