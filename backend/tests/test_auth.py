import pytest
from fastapi.testclient import TestClient

from snapback import auth
from snapback.api import SESSION_COOKIE, create_app
from snapback.auth import AuthUnavailable, LoginThrottle, SessionStore, smb_verify
from snapback.config import Config, RootConfig
from smbprotocol.exceptions import SMBConnectionClosed, SMBException


@pytest.fixture
def make_client(ds):
    (ds.mountpoint / "S").mkdir()
    config = Config(
        server_label="NAS01",
        host="127.0.0.1",
        port=0,
        roots=(RootConfig("s", "S", str(ds.mountpoint / "S")),),
        snapshot_prefixes=("auto-",),
    )

    def make(verify=lambda u, p: (u, p) == ("alice", "secret")):
        return TestClient(create_app(config, verify=verify))

    return make


def login(client, username="alice", password="secret"):
    return client.post("/api/login", json={"username": username, "password": password})


def test_info_before_sign_in_reveals_only_the_server_name(make_client):
    assert make_client().get("/api/info").json() == {"server_label": "NAS01", "user": None, "roots": []}


@pytest.mark.parametrize("url", ["/api/roots/s/points", "/api/roots/s/folder", "/api/roots/s/history?path=a"])
def test_content_needs_a_session(make_client, url):
    r = make_client().get(url)
    assert (r.status_code, r.json()) == (401, {"error": "not_signed_in"})


def test_unknown_root_does_not_leak_before_sign_in(make_client):
    assert make_client().get("/api/roots/nope/folder").status_code == 401


def test_sign_in(make_client):
    client = make_client()
    r = login(client)
    assert (r.status_code, r.json()) == (200, {"user": "alice"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert client.get("/api/info").json()["user"] == "alice"
    assert client.get("/api/roots/s/folder").status_code == 200


def test_forged_cookie_is_refused(make_client):
    client = make_client()
    client.cookies.set(SESSION_COOKIE, "forged")
    assert client.get("/api/roots/s/folder").status_code == 401


def test_sign_out(make_client):
    client = make_client()
    login(client)
    token = client.cookies.get(SESSION_COOKIE)
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/roots/s/folder").status_code == 401
    client.cookies.set(SESSION_COOKIE, token)  # the old token is dead on the server too
    assert client.get("/api/roots/s/folder").status_code == 401


@pytest.mark.parametrize("username,password", [("alice", "wrong"), ("bob", "secret"), ("", "secret"), ("  ", "x"), ("alice", "")])
def test_bad_credentials(make_client, username, password):
    client = make_client()
    r = login(client, username, password)
    assert (r.status_code, r.json()) == (401, {"error": "bad_credentials"})
    assert SESSION_COOKIE not in client.cookies


def test_empty_values_never_reach_the_smb_server(make_client):
    calls = []
    client = make_client(lambda u, p: calls.append(u) or True)
    login(client, "", "x")
    login(client, "alice", "")
    assert calls == []


def test_malformed_request_does_not_echo_the_password(make_client):
    client = make_client()
    r = client.post("/api/login", json={"password": "hunter2-secret"})
    assert (r.status_code, r.json()) == (400, {"error": "bad_request"})
    assert "hunter2" not in r.text
    r = client.post("/api/login", json={"username": "a", "password": "x" * 2000})
    assert r.status_code == 400 and "xxxx" not in r.text


def test_repeated_failures_stop_reaching_the_smb_server(make_client):
    calls = []

    def verify(u, p):
        calls.append(u)
        return False

    client = make_client(verify)
    assert [login(client, "Alice", "x").status_code for _ in range(5)] == [401] * 5
    r = login(client, "alice", "x")  # same account whatever the case
    assert (r.status_code, r.json()) == (429, {"error": "too_many_attempts"})
    assert len(calls) == 5


def test_success_clears_failures(make_client):
    client = make_client()
    for _ in range(4):
        login(client, "alice", "wrong")
    assert login(client).status_code == 200
    for _ in range(4):
        assert login(client, "alice", "wrong").status_code == 401


def test_smb_server_unreachable(make_client):
    def verify(u, p):
        raise AuthUnavailable("ValueError")

    r = login(make_client(verify))
    assert (r.status_code, r.json()) == (503, {"error": "auth_unavailable"})


def test_session_expires_when_idle_and_slides_with_use():
    t = [0.0]
    store = SessionStore(idle_seconds=100, clock=lambda: t[0])
    token = store.create("alice")
    t[0] = 90
    assert store.get(token) == "alice"
    t[0] = 180  # 90 s after the last use
    assert store.get(token) == "alice"
    t[0] = 281
    assert store.get(token) is None
    assert store.get(None) is None


def test_throttle_forgets_after_the_window():
    t = [0.0]
    throttle = LoginThrottle(2, 60, clock=lambda: t[0])
    throttle.failed("a")
    throttle.failed("a")
    assert throttle.blocked("a") and not throttle.blocked("b")
    t[0] = 61
    assert not throttle.blocked("a")


class FakeConnection:
    connect_error = None
    created = None

    def __init__(self, guid, host, port, require_signing):
        FakeConnection.created = self
        self.host, self.port, self.require_signing = host, port, require_signing
        self.closed = False

    def connect(self, timeout):
        if self.connect_error:
            raise self.connect_error

    def disconnect(self):
        self.closed = True


class FakeSession:
    connect_error = None

    def __init__(self, conn, username, password, require_encryption, auth_protocol):
        assert auth_protocol == "ntlm"

    def connect(self):
        if self.connect_error:
            raise self.connect_error

    def disconnect(self):
        pass


@pytest.fixture
def fake_smb(monkeypatch):
    monkeypatch.setattr(auth, "Connection", FakeConnection)
    monkeypatch.setattr(auth, "Session", FakeSession)
    monkeypatch.setattr(FakeConnection, "connect_error", None)
    monkeypatch.setattr(FakeSession, "connect_error", None)
    return monkeypatch


def test_smb_verify_accepts_and_requires_signing(fake_smb):
    assert smb_verify("nas", "alice", "pw") is True
    assert FakeConnection.created.require_signing is True
    assert FakeConnection.created.closed


def test_smb_verify_refuses_guest_and_logon_failures(fake_smb):
    fake_smb.setattr(FakeSession, "connect_error", SMBException("authenticated as a guest"))
    assert smb_verify("nas", "alice", "pw") is False
    assert FakeConnection.created.closed


@pytest.mark.parametrize("error", [ValueError("Failed to connect"), OSError("no route")])
def test_smb_verify_unreachable(fake_smb, error):
    fake_smb.setattr(FakeConnection, "connect_error", error)
    with pytest.raises(AuthUnavailable):
        smb_verify("nas", "alice", "pw")


def test_smb_connection_lost_is_not_a_wrong_password(fake_smb):
    fake_smb.setattr(FakeSession, "connect_error", SMBConnectionClosed("closed"))
    with pytest.raises(AuthUnavailable):
        smb_verify("nas", "alice", "pw")
