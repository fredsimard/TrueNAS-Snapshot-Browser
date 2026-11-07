"""Everything that reads a share, as requests a worker answers (PLAN.md §8).

A worker runs with the signed-in user's identity, so the kernel applies the share's permissions to
every listing and stat here. Requests and answers are plain JSON values, so they can cross a socket;
answers are already in the API's response shape.

    {"op": "points",  "root": id}
    {"op": "folder",  "root": id, "path": rel, "at": point id}
    {"op": "history", "root": id, "path": rel}

    → {"ok": value} or {"error": code}
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from .browse import FolderResult, History, Row, Timeline, file_history, list_folder
from .config import Config
from .paths import Entry, PathRejected, Root, parse_rel, resolve_root
from .snapshots import SnapshotIndex

log = logging.getLogger(__name__)

# HTTP status for each error code an answer can carry.
ERROR_STATUS = {"bad_request": 400, "bad_path": 400, "unknown_root": 404, "unknown_point": 404}


class OpError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Ops:
    def __init__(self, roots: Mapping[str, Root], index: SnapshotIndex):
        self.roots = dict(roots)
        self.index = index

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
        return cls(roots, index)

    def handle(self, req: Mapping) -> dict:
        try:
            return {"ok": self._run(req)}
        except OpError as e:
            return {"error": e.code}
        except Exception:
            log.exception("request %s failed", req.get("op"))
            return {"error": "internal"}

    def _run(self, req: Mapping) -> dict:
        root = self.roots.get(req.get("root"))
        if root is None:
            raise OpError("unknown_root")
        tl = Timeline(root, self.index.list(root.snapshot_dir))
        op = req.get("op")
        if op == "points":
            return {"points": [{"id": s.id, "time": s.time.isoformat()} for s in tl.snapshots]}
        parts = _parts(req.get("path"))
        if op == "folder":
            at = tl.index_of(str(req.get("at", "now")))
            if at is None:
                raise OpError("unknown_point")
            return folder_json(tl, list_folder(tl, parts, at))
        if op == "history":
            if not parts:
                raise OpError("bad_path")
            return history_json(tl, file_history(tl, parts))
        raise OpError("bad_request")


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


def history_json(tl: Timeline, h: History) -> dict:
    return {
        "now": entry_json(h.now) if h.now is not None else None,
        "versions": [
            {**entry_json(v.entry), "current": v.current, "points": [tl.point_id(i) for i in v.points]}
            for v in h.versions
        ],
    }
