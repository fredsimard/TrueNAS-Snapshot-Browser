from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from snapshots_browser.snapshots import SnapshotIndex, snapshot_id, time_from_name

TZ = ZoneInfo("America/Toronto")


@pytest.mark.parametrize(
    "name,expected",
    [
        ("auto-2026-09-19_00-00", datetime(2026, 9, 19, 0, 0)),
        ("auto-2026-09-19_14-05", datetime(2026, 9, 19, 14, 5)),
        ("zfs-auto-snap_hourly-2026-09-19-1400", datetime(2026, 9, 19, 14, 0)),
        ("autosnap_2026-09-19_14:00:01_hourly", datetime(2026, 9, 19, 14, 0, 1)),
        ("daily-20260919T1400", datetime(2026, 9, 19, 14, 0)),
        ("weekly-2026-09-19", datetime(2026, 9, 19)),
        ("task3-2026-13-40_00-00-then-2026-09-19_01-00", datetime(2026, 9, 19, 1, 0)),
    ],
)
def test_time_from_name(name, expected):
    assert time_from_name(name, TZ) == expected.replace(tzinfo=TZ)


@pytest.mark.parametrize("name", ["manual", "before-upgrade", "auto-123"])
def test_time_from_name_none(name):
    assert time_from_name(name, TZ) is None


def make(ds, *names):
    for n in names:
        (ds.snapdir / n).mkdir()


def test_prefix_filter_sort_and_dedupe(ds):
    make(
        ds,
        "auto-2026-09-21_00-00",
        "auto-2026-09-20_00-00",
        "daily-2026-09-20_00-00",  # same minute as auto-…-20: one point
        "hourly-2026-09-21_13-00",
        "manual-2026-09-22_00-00",  # not an allowed prefix
        "before-upgrade",
    )
    idx = SnapshotIndex(("auto-", "hourly-", "daily-"), 500, 30, TZ)
    snaps = idx.list(str(ds.snapdir))
    assert [s.name for s in snaps] == ["auto-2026-09-20_00-00", "auto-2026-09-21_00-00", "hourly-2026-09-21_13-00"]
    assert snaps[0].id == snapshot_id("auto-2026-09-20_00-00")
    assert len(snaps[0].id) == 16 and "auto" not in snaps[0].id


def test_unparsable_name_uses_mtime(ds):
    make(ds, "auto-manual")
    snaps = SnapshotIndex(("auto-",), 500, 30, TZ).list(str(ds.snapdir))
    assert [s.name for s in snaps] == ["auto-manual"]
    assert snaps[0].time.tzinfo is not None


def test_cap_keeps_newest(ds):
    make(ds, *[f"auto-2026-09-{d:02d}_00-00" for d in range(1, 11)])
    snaps = SnapshotIndex(("auto-",), 3, 30, TZ).list(str(ds.snapdir))
    assert [s.name[5:15] for s in snaps] == ["2026-09-08", "2026-09-09", "2026-09-10"]


def test_cache_ttl(ds):
    t = [0.0]
    idx = SnapshotIndex(("auto-",), 500, 30, TZ, clock=lambda: t[0])
    make(ds, "auto-2026-09-01_00-00")
    assert len(idx.list(str(ds.snapdir))) == 1
    make(ds, "auto-2026-09-02_00-00")
    t[0] = 29
    assert len(idx.list(str(ds.snapdir))) == 1
    t[0] = 31
    assert len(idx.list(str(ds.snapdir))) == 2
