"""Snapshot enumeration from <dataset_mountpoint>/.zfs/snapshot. No zfs CLI.

Only snapshots whose names start with one of SNAPSHOT_PREFIXES are ever returned. Clients refer to
snapshots by an opaque id derived from the name; names never leave the server.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .paths import is_valid_name

log = logging.getLogger(__name__)

# 2026-09-19_00-00 (TrueNAS default), 2026-09-19-0000 (zfs-auto-snapshot),
# 2026-09-19_00:00:01 (sanoid), 20260919T0000, 2026-09-19 (date only).
NAME_TIME_RE = re.compile(
    r"(?<!\d)(\d{4})-?(\d{2})-?(\d{2})(?:[_T\-. ](\d{2})[-:.]?(\d{2})(?:[-:.]?(\d{2}))?)?(?!\d)"
)


@dataclass(frozen=True)
class Snapshot:
    id: str
    name: str
    time: datetime  # timezone-aware


def snapshot_id(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]


def time_from_name(name: str, tz: ZoneInfo | None) -> datetime | None:
    for m in NAME_TIME_RE.finditer(name):
        y, mo, d, h, mi, s = (int(g) if g else 0 for g in m.groups())
        if not 1990 <= y <= 2200:
            continue
        try:
            naive = datetime(y, mo, d, h, mi, s)
        except ValueError:
            continue
        return naive.replace(tzinfo=tz) if tz is not None else naive.astimezone()
    return None


class SnapshotIndex:
    """Filtered, time-ordered snapshot lists per dataset, cached briefly (PLAN.md §12 risk 5)."""

    def __init__(
        self,
        prefixes: tuple[str, ...],
        max_listed: int,
        ttl_seconds: float,
        tz: ZoneInfo | None,
        clock=time.monotonic,
    ):
        self._prefixes = prefixes
        self._max_listed = max_listed
        self._ttl = ttl_seconds
        self._tz = tz
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, tuple[Snapshot, ...]]] = {}

    def list(self, snapshot_dir: str) -> tuple[Snapshot, ...]:
        """Oldest first."""
        now = self._clock()
        with self._lock:
            hit = self._cache.get(snapshot_dir)
            if hit and now - hit[0] < self._ttl:
                return hit[1]
        snaps = self._enumerate(snapshot_dir)
        with self._lock:
            self._cache[snapshot_dir] = (now, snaps)
        return snaps

    def _enumerate(self, snapshot_dir: str) -> tuple[Snapshot, ...]:
        found: list[Snapshot] = []
        for name in os.listdir(snapshot_dir):
            if not name.startswith(self._prefixes) or name.startswith(".") or not is_valid_name(name):
                continue
            t = time_from_name(name, self._tz)
            if t is None:
                # Fallback only: stat may automount the snapshot.
                try:
                    t = datetime.fromtimestamp(os.lstat(os.path.join(snapshot_dir, name)).st_mtime).astimezone()
                except OSError:
                    continue
                log.warning("snapshot %s: no timestamp in name, using directory mtime", name)
            found.append(Snapshot(id=snapshot_id(name), name=name, time=t))
        found.sort(key=lambda s: (s.time, s.name))
        # Snapshots taken at the same minute by different tasks are the same point in time for users.
        deduped: list[Snapshot] = []
        for s in found:
            if deduped and deduped[-1].time == s.time:
                continue
            deduped.append(s)
        return tuple(deduped[-self._max_listed :])
