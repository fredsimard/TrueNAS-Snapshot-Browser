import asyncio
import io
import logging
import os
import threading
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from snapshots_browser import downloads, transfer
from snapshots_browser.api import create_app
from snapshots_browser.config import Config, RootConfig
from snapshots_browser.downloads import TooLarge, download_name, plan_zip, start_zip, zip_complete
from snapshots_browser.ops import Ops
from snapshots_browser.paths import Missing
from snapshots_browser.transfer import DownloadAborted, content_disposition
from snapshots_browser.workers import InProcessBackend

TZ = ZoneInfo("America/Toronto")
SNAP = "auto-2026-09-20_14-00"


def read_all(fd):
    chunks = []
    while chunk := os.read(fd, 1 << 16):
        chunks.append(chunk)
    os.close(fd)
    return b"".join(chunks)


@pytest.mark.parametrize("name,want", [
    ("Logo_final.psd", "Logo_final (2026-09-12 14.00).psd"),
    ("archive.tar.gz", "archive.tar (2026-09-12 14.00).gz"),
    ("README", "README (2026-09-12 14.00)"),
    ("Retouched.zip", "Retouched (2026-09-12 14.00).zip"),
])
def test_download_name(name, want):
    assert download_name(name, datetime(2026, 9, 12, 14, 0, 59)) == want


def test_content_disposition_never_breaks_the_header():
    h = content_disposition('Été "final"\\.pdf')
    assert h.startswith('attachment; filename="_t_ _final__.pdf"; ')
    assert h.endswith("filename*=UTF-8''%C3%89t%C3%A9%20%22final%22%5C.pdf")
    assert "\n" not in content_disposition("a\nb")


@pytest.fixture
def tree(ds):
    b = ds.blob(b"x" * 10, 1_700_000_000)
    ds.populate(None, {
        "Share/Job/a.txt": b,
        "Share/Job/sub/b.txt": b,
        "Share/Job/empty": None,
        "Share/Job/.hidden": b,
        "Share/Job/locked/c.txt": b,
        "Share/other.txt": b,
    })
    job = ds.mountpoint / "Share" / "Job"
    os.symlink("/etc", job / "link")
    os.chmod(job / "locked", 0)
    yield str(ds.mountpoint / "Share")
    os.chmod(job / "locked", 0o755)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")
def test_plan_takes_what_the_account_can_read(tree):
    plan = plan_zip(tree, (), ["Job"], 10**9)
    assert [("/".join(i.arc), i.is_dir) for i in plan.items] == [
        ("Job", True), ("Job/a.txt", False), ("Job/empty", True), ("Job/sub", True), ("Job/sub/b.txt", False),
    ]  # no hidden file, no symlink, no folder it can't open
    assert (plan.bytes, plan.files, plan.folders) == (20, 2, 3)


def test_plan_stops_early_when_too_large(tree, monkeypatch):
    with pytest.raises(TooLarge):
        plan_zip(tree, (), ["Job", "other.txt"], 15)
    monkeypatch.setattr(downloads, "MAX_ZIP_ENTRIES", 2)
    with pytest.raises(TooLarge):
        plan_zip(tree, (), ["Job"], 10**9)


def test_a_fifo_is_refused_without_waiting_for_a_writer(tmp_path):
    os.mkfifo(tmp_path / "pipe")
    (tmp_path / "file").write_bytes(b"abc")
    outcome = []

    def run():
        try:
            downloads.open_file(str(tmp_path), ("pipe",))
        except Missing:
            outcome.append("missing")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(5)
    assert outcome == ["missing"]  # still blocked in open() otherwise
    fd, st = downloads.open_file(str(tmp_path), ("file",))
    try:
        assert os.get_blocking(fd) and os.read(fd, 10) == b"abc" and st.st_size == 3
    finally:
        os.close(fd)


def test_zip_contents(tree):
    plan = plan_zip(tree, (), ["Job", "other.txt"], 10**9)
    data = read_all(start_zip(tree, (), plan, TZ))
    assert zip_complete(data)
    zf = zipfile.ZipFile(io.BytesIO(data))
    assert zf.testzip() is None
    names = zf.namelist()
    assert "Job/" in names and "Job/empty/" in names and "Job/sub/b.txt" in names and "other.txt" in names
    assert zf.read("Job/a.txt") == b"x" * 10
    info = zf.getinfo("Job/a.txt")
    assert info.compress_type == zipfile.ZIP_STORED
    assert info.date_time == (2023, 11, 14, 17, 13, 20)  # 1_700_000_000 in Toronto


def test_a_zip_that_fails_midway_is_not_complete(tree, monkeypatch):
    def broken(src, dst, length=0):
        raise OSError("read error")

    monkeypatch.setattr(downloads.shutil, "copyfileobj", broken)
    plan = plan_zip(tree, (), ["Job"], 10**9)
    assert not zip_complete(read_all(start_zip(tree, (), plan, TZ)))


@pytest.fixture
def client(ds):
    old = ds.blob(b"old version", 1_700_000_000)
    new = ds.blob(b"new", 1_700_100_000)
    ds.populate(SNAP, {"Projects/LOGOS/logo.psd": old, "Projects/LOGOS/notes.txt": old, "Projects/LOGOS/sub/x.txt": old})
    ds.populate(None, {"Projects/LOGOS/logo.psd": new, "Projects/Locked/secret.txt": new})
    config = Config(
        server_label="NAS01", host="127.0.0.1", port=0,
        roots=(RootConfig("projects", "Projects", str(ds.mountpoint / "Projects")),),
        snapshot_prefixes=("auto-",), timezone=TZ, max_download_bytes=1000,
    )
    c = TestClient(create_app(config, verify=lambda u, p, s: set(s), backend=InProcessBackend(Ops.from_config(config))))
    c.post("/api/login", json={"username": "alice", "password": "pw"})
    c.snap = c.get("/api/roots/projects/points").json()["points"][0]["id"]
    return c


def prepare(client, names, path="LOGOS", at="now"):
    return client.post("/api/roots/projects/downloads", json={"path": path, "at": at, "names": names})


def test_file_download(client, caplog):
    r = prepare(client, ["logo.psd"], at=client.snap)
    body = r.json()
    assert body["kind"] == "file" and body["bytes"] == 11
    assert body["name"] == "logo (2023-11-14 17.13).psd"  # the version's modified time
    with caplog.at_level(logging.INFO, "snapshots_browser.audit"):
        got = client.get(f"/api/downloads/{body['id']}")
    assert got.content == b"old version"
    assert got.headers["content-length"] == "11"
    assert got.headers["content-disposition"] == content_disposition("logo (2023-11-14 17.13).psd")
    assert client.get(f"/api/downloads/{body['id']}/progress").json() == {"state": "done", "sent": 11, "total": 11}
    line = caplog.records[-1].getMessage()
    assert "download done by 'alice': file" in line and SNAP in line and "/Projects/LOGOS/logo.psd" in line


def test_a_single_file_among_names_that_are_gone_downloads_that_file(client):
    # The first name is gone, so the plan is for notes.txt: that is what must be fetched.
    r = prepare(client, ["gone.txt", "notes.txt"], at=client.snap).json()
    assert r["kind"] == "file" and r["name"] == "notes (2023-11-14 17.13).txt"
    assert "item" not in r
    got = client.get(f"/api/downloads/{r['id']}")
    assert got.content == b"old version"
    assert client.get(f"/api/downloads/{r['id']}/progress").json()["state"] == "done"


def test_the_audit_line_cannot_be_forged_through_names(client, caplog):
    forged = "x\n2026-11-07 12:00:00,000 INFO snapshots_browser.audit: download done by 'bob': file salaries.xlsx"
    r = prepare(client, ["logo.psd", "notes.txt", forged], at=client.snap).json()
    assert r["kind"] == "zip"
    with caplog.at_level(logging.INFO, "snapshots_browser.audit"):
        client.get(f"/api/downloads/{r['id']}")
    (record,) = caplog.records
    line = record.getMessage()
    assert "\n" not in line and "salaries" not in line  # not a name that was downloaded
    assert "logo.psd" in line and "notes.txt" in line


def test_the_audit_line_escapes_names_on_the_share(client, ds, caplog):
    blob = ds.blob(b"x", 1_700_000_000)
    os.link(blob.path, ds.mountpoint / "Projects" / "LOGOS" / "we\nird\r.txt")
    r = prepare(client, ["we\nird\r.txt"]).json()
    assert r["kind"] == "file"
    with caplog.at_level(logging.INFO, "snapshots_browser.audit"):
        assert client.get(f"/api/downloads/{r['id']}").content == b"x"
    line = caplog.records[-1].getMessage()
    assert "\n" not in line and "\r" not in line
    assert "we\\nird\\r.txt" in line


def test_folder_downloads_as_a_zip(client, tmp_path):
    r = prepare(client, ["LOGOS"], path="", at=client.snap).json()
    assert (r["kind"], r["name"], r["files"], r["folders"]) == ("zip", "LOGOS (2026-09-20 14.00).zip", 3, 2)
    got = client.get(f"/api/downloads/{r['id']}")
    assert got.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(got.content))
    assert sorted(zf.namelist()) == ["LOGOS/", "LOGOS/logo.psd", "LOGOS/notes.txt", "LOGOS/sub/", "LOGOS/sub/x.txt"]
    for text in (str(r), got.headers.get("content-disposition", "")):
        assert str(tmp_path) not in text and SNAP not in text


def test_several_items_zip_under_the_folder_name(client):
    r = prepare(client, ["logo.psd", "notes.txt"], at=client.snap).json()
    assert (r["kind"], r["name"]) == ("zip", "LOGOS (2026-09-20 14.00).zip")
    zf = zipfile.ZipFile(io.BytesIO(client.get(f"/api/downloads/{r['id']}").content))
    assert sorted(zf.namelist()) == ["logo.psd", "notes.txt"]


def test_nothing_to_download(client):
    assert prepare(client, ["Big"], path="").json() == {"error": "not_found"}
    assert prepare(client, ["x"], path="Nope").json() == {"error": "not_found"}


def test_limit(ds):
    blob = ds.blob(b"x" * 600, 1_700_000_000)
    ds.populate(None, {"P/A/1": blob, "P/A/2": blob})
    config = Config(server_label="N", host="h", port=0, roots=(RootConfig("p", "P", str(ds.mountpoint / "P")),),
                    snapshot_prefixes=("auto-",), max_download_bytes=1000)
    c = TestClient(create_app(config, verify=lambda u, p, s: set(s), backend=InProcessBackend(Ops.from_config(config))))
    c.post("/api/login", json={"username": "alice", "password": "pw"})
    r = c.post("/api/roots/p/downloads", json={"path": "", "names": ["A"]})
    assert r.json() == {"too_large": True, "limit": 1000}
    assert c.post("/api/roots/p/downloads", json={"path": "A", "names": ["1"]}).json()["kind"] == "file"


def test_downloads_start_once(client):
    r = prepare(client, ["notes.txt"], at=client.snap).json()
    assert client.get(f"/api/downloads/{r['id']}").status_code == 200
    # Opened by a navigation: errors are 204 No Content, so the browser keeps the page.
    again = client.get(f"/api/downloads/{r['id']}")
    assert (again.status_code, again.content) == (204, b"")


def test_downloads_belong_to_their_session(client):
    r = prepare(client, ["notes.txt"], at=client.snap).json()
    other = TestClient(client.app)
    other.post("/api/login", json={"username": "bob", "password": "pw"})
    assert other.get(f"/api/downloads/{r['id']}").status_code == 204
    assert other.get(f"/api/downloads/{r['id']}/progress").status_code == 404
    assert TestClient(client.app).get(f"/api/downloads/{r['id']}").status_code == 204
    assert client.get(f"/api/downloads/{r['id']}/progress").json()["state"] == "ready"  # untouched


def pipe_with(data: bytes) -> int:
    r, w = os.pipe()
    os.write(w, data)
    os.close(w)
    return r


def fd_closed(fd):
    try:
        os.fstat(fd)
    except OSError:
        return True
    return False


def test_cancel_stops_a_running_download(monkeypatch):
    monkeypatch.setattr(transfer, "CHUNK", 4)
    d = transfer.Download("id", "alice", {}, "file", "n", 12)
    fd = pipe_with(b"x" * 12)
    it = transfer.body(d, fd, 12)
    assert next(it) == b"xxxx"
    d.state = "cancelled"  # what DELETE /api/downloads/{id} does
    with pytest.raises(DownloadAborted):
        next(it)
    assert d.state == "cancelled" and d.sent == 4 and fd_closed(fd)


def test_browser_stopping_a_download():
    d = transfer.Download("id", "alice", {}, "file", "n", 3)
    it = transfer.body(d, pipe_with(b"abc"), 3)
    next(it)
    it.close()
    assert d.state == "cancelled"


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
def test_a_client_that_leaves_before_the_stream_starts_releases_the_download(spec_version, caplog):
    # The body generator never runs then, so its own cleanup can't be relied on.
    d = transfer.Download("id", "alice", {}, "file", "n", 3)
    d.state = "running"
    fd = pipe_with(b"abc")
    response = transfer.DownloadResponse(d, fd, 3, media_type="application/octet-stream")

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        if spec_version == "2.4":
            raise OSError("client gone")

    scope = {"type": "http", "asgi": {"spec_version": spec_version}}
    with caplog.at_level(logging.INFO, "snapshots_browser.audit"):
        try:
            asyncio.run(response(scope, receive, send))
        except ClientDisconnect:
            pass
    assert d.state == "cancelled" and fd_closed(fd)
    assert [r.getMessage().startswith("download cancelled by 'alice'") for r in caplog.records] == [True]


def test_releasing_twice_logs_and_closes_once(caplog):
    d = transfer.Download("id", "alice", {}, "file", "n", 3)
    fd = pipe_with(b"abc")
    with caplog.at_level(logging.INFO, "snapshots_browser.audit"):
        assert b"".join(transfer.body(d, fd, 3)) == b"abc"
        transfer.release(d, fd)  # what the response does afterwards
    assert d.state == "done" and len(caplog.records) == 1


def test_a_file_that_shrank_fails():
    d = transfer.Download("id", "alice", {}, "file", "n", 10)
    with pytest.raises(DownloadAborted):
        list(transfer.body(d, pipe_with(b"abc"), 10))
    assert d.state == "failed"


def test_a_file_that_grew_is_cut_at_its_size():
    d = transfer.Download("id", "alice", {}, "file", "n", 3)
    assert b"".join(transfer.body(d, pipe_with(b"abcdef"), 3)) == b"abc"
    assert d.state == "done"


def test_an_unfinished_zip_fails(caplog):
    d = transfer.Download("id", "alice", {}, "zip", "n.zip", 10)
    with caplog.at_level(logging.INFO, "snapshots_browser.audit"), pytest.raises(DownloadAborted):
        list(transfer.body(d, pipe_with(b"PK\x03\x04 not finished"), None))
    assert d.state == "failed"
    assert "download failed by 'alice': zip" in caplog.records[-1].getMessage()


@pytest.mark.parametrize("names", [["../x"], [".zfs"], ["a/b"], [""], []])
def test_bad_names(client, names):
    assert prepare(client, names).status_code == 400


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")
def test_denied(client, ds):
    locked = ds.mountpoint / "Projects" / "Locked"
    os.chmod(locked, 0)
    try:
        assert prepare(client, ["secret.txt"], path="Locked").json() == {"error": "denied"}
    finally:
        os.chmod(locked, 0o755)


def test_history_says_what_each_version_saves_as(client):
    h = client.get("/api/roots/projects/history", params={"path": "LOGOS/logo.psd"}).json()
    assert [v["saves_as"] for v in h["versions"]] == ["logo (2023-11-14 17.13).psd"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")
def test_a_download_that_cant_start_fails_quietly(client, ds):
    r = prepare(client, ["logo.psd"]).json()
    os.chmod(ds.mountpoint / "Projects" / "LOGOS", 0)  # locked between the dialog and the download
    try:
        got = client.get(f"/api/downloads/{r['id']}")
    finally:
        os.chmod(ds.mountpoint / "Projects" / "LOGOS", 0o755)
    assert got.status_code == 204
    assert client.get(f"/api/downloads/{r['id']}/progress").json()["state"] == "failed"
