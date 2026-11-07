"""Browsing a folder at a point in time, compared with now, and a file's history.

A timeline is the filtered snapshots, oldest first, followed by Now. Points are addressed by
index; index `now` (== len(snapshots)) is the live share. Searches across time avoid touching
every snapshot, because each first access automounts it (PLAN.md §12 risk 5).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .paths import Denied, Entry, Missing, Root, folder_exists, list_names, observe_file, read_dir
from .snapshots import Snapshot


class Timeline:
    def __init__(self, root: Root, snapshots: Sequence[Snapshot]):
        self.root = root
        self.snapshots = tuple(snapshots)
        self.now = len(self.snapshots)

    def base(self, i: int) -> str:
        return self.root.base(None if i == self.now else self.snapshots[i].name)

    def point_id(self, i: int) -> str:
        return "now" if i == self.now else self.snapshots[i].id

    def index_of(self, point_id: str) -> int | None:
        if point_id == "now":
            return self.now
        for i, s in enumerate(self.snapshots):
            if s.id == point_id:
                return i
        return None


@dataclass
class Row:
    entry: Entry
    status: str | None = None  # "unchanged" | "changed" | "deleted"; None at Now or when unknown
    now: Entry | None = None
    last_seen: int | None = None  # for deleted rows: last snapshot index that has it


@dataclass
class FolderResult:
    state: str  # "ok" | "missing" | "denied"
    rows: list[Row] = field(default_factory=list)
    nearest: int | None = None  # for "missing": closest point where the folder exists
    direction: str | None = None  # "later" | "earlier"


def list_folder(tl: Timeline, parts: Sequence[str], at: int) -> FolderResult:
    try:
        then = read_dir(tl.base(at), parts)
    except Missing:
        nearest, direction = nearest_with_folder(tl, parts, at)
        return FolderResult(state="missing", nearest=nearest, direction=direction)
    except Denied:
        return FolderResult(state="denied")

    if at == tl.now:
        return FolderResult(state="ok", rows=[Row(e) for e in then.values()])

    now_known = True
    try:
        now = read_dir(tl.base(tl.now), parts)
    except Missing:
        now = {}
    except Denied:
        now, now_known = {}, False

    rows: list[Row] = []
    deleted: list[str] = []
    for e in then.values():
        if not now_known:
            rows.append(Row(e))
            continue
        n = now.get(e.name)
        if n is None:
            rows.append(Row(e, "deleted"))
            deleted.append(e.name)
        else:
            rows.append(Row(e, "changed" if n.content != e.content else "unchanged", n))

    if deleted:
        seen = last_seen(tl, parts, at, deleted, known={at: set(then)})
        for r in rows:
            if r.status == "deleted":
                r.last_seen = seen[r.entry.name]
    return FolderResult(state="ok", rows=rows)


def last_seen(
    tl: Timeline, parts: Sequence[str], at: int, names: Sequence[str], known: dict[int, set[str]]
) -> dict[str, int]:
    """Last snapshot index at or after `at` in which each name exists in the folder.

    Bisects between `at` (present) and Now (absent). Every name probes the same midpoints first,
    so one folder listing per probed snapshot serves all names.
    """
    listings = dict(known)

    def names_at(i: int) -> set[str]:
        if i not in listings:
            try:
                listings[i] = list_names(tl.base(i), parts)
            except (Missing, Denied):
                listings[i] = set()
        return listings[i]

    out: dict[str, int] = {}
    for name in names:
        lo, hi = at, tl.now
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if name in names_at(mid):
                lo = mid
            else:
                hi = mid
        out[name] = lo
    return out


def nearest(start: int, limit: int, pred: Callable[[int], bool]) -> int | None:
    """Closest index from start (exclusive) toward limit (inclusive) where pred holds.

    Gallops outward (1, 2, 4, …) then bisects, so the cost is logarithmic in the distance.
    Assumes pred changes at most once between start and the first hit, which holds for a folder
    that was created (or deleted) once.
    """
    d = 1 if limit > start else -1
    if limit == start:
        return None
    miss, step = start, 1
    while True:
        probe = start + d * step
        if (probe - limit) * d > 0:
            probe = limit
        if pred(probe):
            hit = probe
            while abs(hit - miss) > 1:
                mid = (hit + miss) // 2
                if pred(mid):
                    hit = mid
                else:
                    miss = mid
            return hit
        if probe == limit:
            return None
        miss, step = probe, step * 2


def nearest_with_folder(tl: Timeline, parts: Sequence[str], at: int) -> tuple[int | None, str | None]:
    cache: dict[int, bool] = {}

    def exists(i: int) -> bool:
        if i not in cache:
            cache[i] = folder_exists(tl.base(i), parts)
        return cache[i]

    later = nearest(at, tl.now, exists)
    if later is not None:
        return later, "later"
    earlier = nearest(at, 0, exists)
    if earlier is not None:
        return earlier, "earlier"
    return None, None


@dataclass
class Version:
    entry: Entry
    points: list[int]
    current: bool


@dataclass
class History:
    now: Entry | None
    versions: list[Version]  # newest first


def file_history(tl: Timeline, parts: Sequence[str]) -> History:
    """Distinct versions of a file across all points in time.

    Splits the timeline recursively and stops splitting a range when both ends look identical
    (see Entry.identity and observe_file), so the number of snapshots touched grows with the
    number of changes, not the number of snapshots.
    """
    n = tl.now + 1
    obs: list[tuple[Entry | None, tuple] | None] = [None] * n

    def get(i: int) -> tuple[Entry | None, tuple]:
        if obs[i] is None:
            obs[i] = observe_file(tl.base(i), parts)
        return obs[i]

    def fill(a: int, b: int) -> None:
        same = get(a)[1] == get(b)[1]
        if b - a < 2:
            return
        if same:
            for i in range(a + 1, b):
                if obs[i] is None:
                    obs[i] = obs[a]
            return
        mid = (a + b) // 2
        fill(a, mid)
        fill(mid, b)

    fill(0, n - 1)

    now_entry = obs[tl.now][0]
    by_content: dict[tuple, Version] = {}
    for i in range(tl.now):
        e = obs[i][0]
        if e is None:
            continue
        v = by_content.get(e.content)
        if v is None:
            current = now_entry is not None and now_entry.content == e.content
            by_content[e.content] = Version(entry=e, points=[i], current=current)
        else:
            v.points.append(i)
    versions = sorted(by_content.values(), key=lambda v: v.points[-1], reverse=True)
    return History(now=now_entry, versions=versions)
