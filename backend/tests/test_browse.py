import pytest

from snapback import browse
from snapback.browse import Timeline, file_history, list_folder, nearest
from snapback.config import RootConfig
from snapback.paths import resolve_root
from snapback.snapshots import SnapshotIndex

DAY = 86_400
T0 = 1_758_340_800

SNAPS = [f"auto-2026-09-{d:02d}_00-00" for d in range(20, 28)]  # 8 snapshots, then Now
NOW = len(SNAPS)


@pytest.fixture
def tl(ds):
    keep = ds.blob(b"same", T0)
    logo1 = ds.blob(b"x" * 100, T0 + DAY)
    logo2 = ds.blob(b"y" * 50, T0 + 4 * DAY)
    gone = ds.blob(b"gone", T0)
    new = ds.blob(b"new", T0 + 6 * DAY)

    def files(i):  # i in 0..NOW
        f = {"S/keep.txt": keep, "S/Empty": None, "S/logo.psd": logo1 if i < 4 else logo2}
        if i <= 5:
            f["S/gone.txt"] = gone
        if i >= 6:
            f["S/new.txt"] = new
        if i >= 5:
            f["S/Archive/a.txt"] = keep
        if i <= 3:
            f["S/Old/o.txt"] = keep
        return f

    for i, name in enumerate(SNAPS):
        ds.populate(name, files(i))
    ds.populate(None, files(NOW))
    ds.populate("manual-2026-09-23_12-00", {"S/secret.txt": keep})  # filtered out

    root = resolve_root(RootConfig("s", "S", str(ds.mountpoint / "S")))
    snaps = SnapshotIndex(("auto-",), 500, 30, None).list(root.snapshot_dir)
    assert [s.name for s in snaps] == SNAPS
    return Timeline(root, snaps)


def rows_by_name(res):
    return {r.entry.name: r for r in res.rows}


def test_now_has_no_status(tl):
    res = list_folder(tl, (), NOW)
    assert res.state == "ok"
    rows = rows_by_name(res)
    assert set(rows) == {"keep.txt", "Empty", "logo.psd", "new.txt", "Archive"}
    assert all(r.status is None for r in rows.values())


def test_change_detection(tl):
    rows = rows_by_name(list_folder(tl, (), 2))
    assert set(rows) == {"keep.txt", "Empty", "logo.psd", "gone.txt", "Old"}  # new.txt, Archive: not yet
    assert rows["keep.txt"].status == "unchanged"
    assert rows["Empty"].status == "unchanged"
    logo = rows["logo.psd"]
    assert logo.status == "changed"
    assert (logo.entry.size, logo.now.size) == (100, 50)
    assert rows["gone.txt"].status == "deleted"
    assert rows["gone.txt"].last_seen == 5
    assert rows["Old"].status == "deleted"
    assert rows["Old"].last_seen == 3


def test_last_seen_at_the_last_point_it_existed(tl):
    rows = rows_by_name(list_folder(tl, (), 5))
    assert rows["gone.txt"].last_seen == 5


def test_empty_folder(tl):
    res = list_folder(tl, ("Empty",), 1)
    assert res.state == "ok" and res.rows == []


def test_folder_did_not_exist_yet(tl):
    res = list_folder(tl, ("Archive",), 1)
    assert (res.state, res.nearest, res.direction) == ("missing", 5, "later")


def test_folder_no_longer_exists(tl):
    res = list_folder(tl, ("Old",), NOW)
    assert (res.state, res.nearest, res.direction) == ("missing", 3, "earlier")


def test_folder_never_existed(tl):
    res = list_folder(tl, ("Nope",), 2)
    assert (res.state, res.nearest) == ("missing", None)


def test_history_versions(tl):
    h = file_history(tl, ("logo.psd",))
    assert h.now.size == 50
    assert [(v.entry.size, v.points, v.current) for v in h.versions] == [
        (50, [4, 5, 6, 7], True),
        (100, [0, 1, 2, 3], False),
    ]


def test_history_of_deleted_file(tl):
    h = file_history(tl, ("gone.txt",))
    assert h.now is None
    assert [(v.points, v.current) for v in h.versions] == [([0, 1, 2, 3, 4, 5], False)]


def test_history_skips_unchanged_ranges(tl, monkeypatch):
    calls = []
    real = browse.observe_file
    monkeypatch.setattr(browse, "observe_file", lambda base, parts: calls.append(base) or real(base, parts))
    h = file_history(tl, ("keep.txt",))
    assert len(calls) == 2  # first snapshot and Now are identical: nothing in between is read
    assert [v.points for v in h.versions] == [list(range(NOW))]


@pytest.mark.parametrize(
    "start,limit,first_true,expected",
    [
        (0, 10, 7, 7),
        (0, 10, 1, 1),
        (0, 10, 10, 10),
        (0, 10, 11, None),
        (3, 3, 0, None),
    ],
)
def test_nearest_forward(start, limit, first_true, expected):
    assert nearest(start, limit, lambda i: i >= first_true) == expected


def test_nearest_backward_probes_few_points():
    probed = []

    def pred(i):
        probed.append(i)
        return i <= 480

    assert nearest(499, 0, pred) == 480
    assert len(probed) < 12
