import pytest
from fastapi.testclient import TestClient

from snapback.api import create_app
from snapback.config import Config, RootConfig

SNAPS = ["auto-2026-09-20_00-00", "auto-2026-09-21_00-00"]


@pytest.fixture
def setup(ds, tmp_path):
    old = ds.blob(b"old", 1_700_000_000)
    new = ds.blob(b"newer", 1_700_100_000)
    for name in SNAPS:
        ds.populate(name, {"Projects/LOGOS/logo.psd": old, "Projects/LOGOS/deleted.txt": old})
    ds.populate(None, {"Projects/LOGOS/logo.psd": new})
    ds.populate("manual-2026-09-22_00-00", {"Projects/x": old})
    config = Config(
        server_label="NAS01",
        host="127.0.0.1",
        port=0,
        roots=(RootConfig("projects", "Projects", str(ds.mountpoint / "Projects")),),
        snapshot_prefixes=("auto-",),
    )
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>app</title>")
    (static / "assets" / "app.js").write_text("console.log(1)")
    client = TestClient(create_app(config, str(static), verify=lambda u, p, shares: set(shares)))
    assert client.post("/api/login", json={"username": "alice", "password": "pw"}).status_code == 200
    return client, str(tmp_path)


def no_leaks(text: str, tmp: str):
    assert tmp not in text
    assert "/mnt/" not in text
    assert "auto-2026" not in text and "manual" not in text


def test_info(setup):
    client, _ = setup
    assert client.get("/api/info").json() == {
        "server_label": "NAS01",
        "user": "alice",
        "roots": [{"id": "projects", "label": "Projects"}],
    }


def test_points_are_opaque(setup):
    client, tmp = setup
    r = client.get("/api/roots/projects/points")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    points = r.json()["points"]
    assert len(points) == 2
    assert points[0]["time"].startswith("2026-09-20T00:00:00")
    no_leaks(r.text, tmp)


def test_folder_as_of_snapshot(setup):
    client, tmp = setup
    first = client.get("/api/roots/projects/points").json()["points"][0]["id"]
    r = client.get("/api/roots/projects/folder", params={"path": "LOGOS", "at": first})
    body = r.json()
    assert body["state"] == "ok"
    rows = {e["name"]: e for e in body["entries"]}
    assert rows["logo.psd"]["status"] == "changed"
    assert rows["logo.psd"]["now"] == {"dir": False, "size": 5, "mtime": 1_700_100_000_000}
    assert rows["deleted.txt"]["status"] == "deleted"
    last = client.get("/api/roots/projects/points").json()["points"][1]["id"]
    assert rows["deleted.txt"]["last_seen"] == last
    no_leaks(r.text, tmp)


def test_folder_now(setup):
    client, _ = setup
    body = client.get("/api/roots/projects/folder").json()
    assert body == {
        "state": "ok",
        "entries": [{"name": "LOGOS", "dir": True, "size": None, "mtime": 1_700_000_000_000,
                     "status": None, "now": None, "last_seen": None}],
        "nearest": None,
    }


def test_missing_folder(setup):
    client, _ = setup
    first = client.get("/api/roots/projects/points").json()["points"][0]["id"]
    body = client.get("/api/roots/projects/folder", params={"path": "LOGOS/nope", "at": first}).json()
    assert body == {"state": "missing", "entries": [], "nearest": None}


@pytest.mark.parametrize("path", ["..", "../..", ".zfs/snapshot", "LOGOS/../..", ".hidden"])
def test_bad_paths(setup, path):
    client, _ = setup
    r = client.get("/api/roots/projects/folder", params={"path": path})
    assert (r.status_code, r.json()) == (400, {"error": "bad_path"})


def test_filtered_snapshot_is_unreachable(setup):
    from snapback.snapshots import snapshot_id

    client, _ = setup
    r = client.get("/api/roots/projects/folder", params={"at": snapshot_id("manual-2026-09-22_00-00")})
    assert (r.status_code, r.json()) == (404, {"error": "unknown_point"})
    r = client.get("/api/roots/projects/folder", params={"at": "manual-2026-09-22_00-00"})
    assert r.status_code == 404


def test_unknown_root(setup):
    client, _ = setup
    r = client.get("/api/roots/nope/folder")
    assert (r.status_code, r.json()) == (404, {"error": "unknown_root"})


def test_history(setup):
    client, tmp = setup
    r = client.get("/api/roots/projects/history", params={"path": "LOGOS/logo.psd"})
    body = r.json()
    assert body["now"] == {"dir": False, "size": 5, "mtime": 1_700_100_000_000}
    assert len(body["versions"]) == 1
    v = body["versions"][0]
    assert (v["size"], v["current"], len(v["points"])) == (3, False, 2)
    no_leaks(r.text, tmp)


def test_history_needs_a_file_path(setup):
    client, _ = setup
    assert client.get("/api/roots/projects/history", params={"path": ""}).status_code == 400


def test_spa_fallback(setup):
    client, _ = setup
    assert client.get("/r/projects/LOGOS").text.startswith("<!doctype html>")
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/api/nope").status_code == 404
