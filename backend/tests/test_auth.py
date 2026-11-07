import threading
import time

import pytest
from fastapi.testclient import TestClient

from snapback import auth
from snapback.api import SESSION_COOKIE, create_app
from snapback.auth import AuthUnavailable, LoginThrottle, SessionStore, account_key, smb_sign_in
from snapback.config import Config, RootConfig
from snapback.ops import Ops
from snapback.workers import InProcessBackend, WorkerUnavailable
from smbprotocol.exceptions import AccessDenied, BadNetworkName, SMBConnectionClosed, SMBException


def alice(u, p, shares):
    """Fake SMB: alice/secret is accepted and may use every share."""
    return set(shares) if (u, p) == ("alice", "secret") else None


@pytest.fixture
def make_client(ds):
    for name in ("S", "T"):
        (ds.mountpoint / name).mkdir()
    base = Config(
        server_label="NAS01",
        host="127.0.0.1",
        port=0,
        roots=(
            RootConfig("s", "S", str(ds.mountpoint / "S")),
            RootConfig("t", "Team", str(ds.mountpoint / "T"), smb_share="TEAM"),
        ),
        snapshot_prefixes=("auto-",),
    )

    def make(verify=alice, accounts=None, backend=None, **overrides):
        config = Config(**{**base.__dict__, **overrides})
        backend = backend or InProcessBackend(Ops.from_config(config), accounts)
        return TestClient(create_app(config, verify=verify, backend=backend))

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
    info = client.get("/api/info").json()
    assert info["user"] == "alice"
    assert info["roots"] == [{"id": "s", "label": "S"}, {"id": "t", "label": "Team"}]
    assert client.get("/api/roots/s/folder").status_code == 200


def test_sign_in_asks_for_each_share_by_its_smb_name(make_client):
    calls = []
    login(make_client(lambda u, p, shares: calls.append(list(shares)) or set()))
    assert calls == [["S", "TEAM"]]


def test_only_shares_samba_allows_are_visible(make_client):
    client = make_client(lambda u, p, shares: {"TEAM"})
    assert login(client).status_code == 200
    assert client.get("/api/info").json()["roots"] == [{"id": "t", "label": "Team"}]
    assert client.get("/api/roots/t/folder").status_code == 200
    for url in ["/api/roots/s/points", "/api/roots/s/folder", "/api/roots/s/history?path=a"]:
        r = client.get(url)
        assert (r.status_code, r.json()) == (404, {"error": "unknown_root"})  # same as a root that doesn't exist


def test_no_shares_still_signs_in(make_client):
    client = make_client(lambda u, p, shares: set())
    assert login(client).status_code == 200
    assert client.get("/api/info").json()["roots"] == []


@pytest.mark.parametrize("username", ["alice", "ALICE", " Alice "])
def test_allowed_users(make_client, username):
    client = make_client(lambda u, p, shares: set(shares), allowed_users=frozenset({"alice", "bob"}))
    assert login(client, username, "pw").status_code == 200


def test_user_not_in_allowed_users(make_client):
    calls = []

    def verify(u, p, shares):
        calls.append(list(shares))
        return set(shares)

    client = make_client(verify, allowed_users=frozenset({"bob"}))
    r = login(client)
    assert (r.status_code, r.json()) == (403, {"error": "not_allowed"})
    assert SESSION_COOKIE not in client.cookies
    assert calls == [[]]  # the password was checked, no share was tried


def test_allowed_users_does_not_reveal_who_is_listed(make_client):
    client = make_client(lambda u, p, shares: None, allowed_users=frozenset({"bob"}))
    assert login(client, "alice", "wrong").json() == login(client, "bob", "wrong").json() == {"error": "bad_credentials"}


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


class SpyBackend:
    """Records each session's runner; `fail` makes requests fail as if the worker died."""

    def __init__(self, fail_start=False, fail_calls=False):
        self.runners = []
        self.fail_start = fail_start
        self.fail_calls = fail_calls

    def start(self, username):
        if self.fail_start:
            raise WorkerUnavailable("supervisor: busy")
        runner = SpyRunner(self)
        self.runners.append(runner)
        return runner


class SpyRunner:
    def __init__(self, backend):
        self.backend = backend
        self.closed = False

    def call(self, req):
        if self.backend.fail_calls:
            raise WorkerUnavailable("worker: TimeoutError")
        return {"ok": {"points": []}}

    def close(self):
        self.closed = True


def test_account_unknown_to_the_host_is_refused(make_client):
    client = make_client(accounts={"bob"})
    r = login(client)
    assert (r.status_code, r.json()) == (403, {"error": "not_allowed"})
    assert SESSION_COOKIE not in client.cookies


def test_sign_out_ends_the_sessions_workers(make_client):
    backend = SpyBackend()
    client = make_client(backend=backend)
    login(client)
    assert client.get("/api/roots/s/points").json() == {"points": []}
    assert not backend.runners[0].closed
    client.post("/api/logout")
    assert backend.runners[0].closed


def test_supervisor_unavailable_at_sign_in(make_client):
    r = login(make_client(backend=SpyBackend(fail_start=True)))
    assert (r.status_code, r.json()) == (503, {"error": "auth_unavailable"})


def test_worker_unavailable(make_client):
    client = make_client(backend=SpyBackend(fail_calls=True))
    login(client)
    r = client.get("/api/roots/s/points")
    assert (r.status_code, r.json()) == (503, {"error": "unavailable"})


def test_requests_for_a_root_the_session_cant_use_never_reach_a_worker(make_client):
    backend = SpyBackend(fail_calls=True)
    client = make_client(lambda u, p, shares: {"S"}, backend=backend)
    login(client)
    assert client.get("/api/roots/t/points").status_code == 404
    assert client.get("/api/roots/nope/points").status_code == 404


def test_expired_sessions_end_their_workers():
    t = [0.0]
    store = SessionStore(idle_seconds=100, clock=lambda: t[0])
    a, b = SpyRunner(None), SpyRunner(None)
    token = store.create("alice", {"s"}, a)
    store.create("bob", {"s"}, b)
    t[0] = 150
    assert store.get(token) is None
    assert a.closed and b.closed  # bob's too, without bob coming back


@pytest.mark.parametrize("username,password", [("alice", "wrong"), ("bob", "secret"), ("", "secret"), ("  ", "x"), ("alice", "")])
def test_bad_credentials(make_client, username, password):
    client = make_client()
    r = login(client, username, password)
    assert (r.status_code, r.json()) == (401, {"error": "bad_credentials"})
    assert SESSION_COOKIE not in client.cookies


def test_empty_values_never_reach_the_smb_server(make_client):
    calls = []
    client = make_client(lambda u, p, shares: calls.append(u) or set())
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

    def verify(u, p, shares):
        calls.append(u)
        return None

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
    def verify(u, p, shares):
        raise AuthUnavailable("ValueError")

    r = login(make_client(verify))
    assert (r.status_code, r.json()) == (503, {"error": "auth_unavailable"})


def test_session_expires_when_idle_and_slides_with_use():
    t = [0.0]
    store = SessionStore(idle_seconds=100, clock=lambda: t[0])
    token = store.create("alice", {"s"})
    t[0] = 90
    assert (store.get(token).username, store.get(token).roots) == ("alice", frozenset({"s"}))
    t[0] = 180  # 90 s after the last use
    assert store.get(token).username == "alice"
    t[0] = 281
    assert store.get(token) is None
    assert store.get(None) is None


def test_throttle_forgets_after_the_window():
    t = [0.0]
    throttle = LoginThrottle(2, 60, clock=lambda: t[0])
    assert throttle.reserve("a") and throttle.reserve("a")
    assert not throttle.reserve("a") and throttle.reserve("b")
    t[0] = 61
    assert throttle.reserve("a")


def test_throttle_gives_an_attempt_back():
    throttle = LoginThrottle(2)
    assert throttle.reserve("a") and throttle.reserve("a")
    throttle.release("a")
    assert throttle.reserve("a") and not throttle.reserve("a")
    throttle.clear("a")
    assert throttle.reserve("a")


@pytest.mark.parametrize("spelling", ["alice", "ALICE", "Alice", r"NAS\alice", r"corp.example\Alice", "alice@corp.example"])
def test_account_key_is_the_same_for_every_spelling(spelling):
    assert account_key(spelling) == "alice"


def test_concurrent_failures_cannot_get_past_the_limit(make_client):
    calls = []
    lock = threading.Lock()

    def slow_refusal(u, p, shares):
        with lock:
            calls.append(u)
        time.sleep(0.2)  # long enough for every request to arrive before the first answer
        return None

    client = make_client(slow_refusal)
    codes = []
    threads = [threading.Thread(target=lambda: codes.append(login(client, "victim", "x").status_code)) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 5
    assert sorted(codes) == [401] * 5 + [429] * 25


def test_every_spelling_of_an_account_counts_against_one_limit(make_client):
    calls = []

    def verify(u, p, shares):
        calls.append(u)
        return None

    client = make_client(verify)
    for name in ("alice", "ALICE", r"NAS\alice", "alice@corp", "Alice"):
        assert login(client, name, "x").status_code == 401
    assert login(client, r"OTHER\Alice", "x").status_code == 429
    assert len(calls) == 5


def test_an_unreachable_server_does_not_use_up_attempts(make_client):
    def unreachable(u, p, shares):
        raise AuthUnavailable("ConnectionRefusedError")

    client = make_client(unreachable)
    assert [login(client, "alice", "secret").status_code for _ in range(30)] == [503] * 30


def test_successful_sign_ins_do_not_use_up_the_address_limit(make_client):
    client = make_client()
    assert [login(client).status_code for _ in range(25)] == [200] * 25
    for _ in range(4):
        assert login(client, "alice", "wrong").status_code == 401


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

    def disconnect(self, close=True):
        assert close is False  # a refused share also refuses the polite disconnect
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


class FakeTree:
    errors: dict[str, Exception] = {}  # raised by connect
    connected: list[str] = []

    def __init__(self, session, path):
        self.path = path
        self.share = path.rsplit("\\", 1)[1]

    def connect(self):
        if self.share in self.errors:
            raise self.errors[self.share]
        FakeTree.connected.append(self.path)


class FakeOpen:
    errors: dict[str, Exception] = {}  # raised by create, as Samba does for a folder the account can't read
    opened: list[tuple] = []

    def __init__(self, tree, name):
        self.tree, self.name = tree, name

    def create(self, impersonation, access, attributes, share_access, disposition, options):
        if self.tree.share in self.errors:
            raise self.errors[self.tree.share]
        FakeOpen.opened.append((self.tree.share, self.name, access, disposition))

    def close(self):
        pass


@pytest.fixture
def fake_smb(monkeypatch):
    monkeypatch.setattr(auth, "Connection", FakeConnection)
    monkeypatch.setattr(auth, "Session", FakeSession)
    monkeypatch.setattr(auth, "TreeConnect", FakeTree)
    monkeypatch.setattr(auth, "Open", FakeOpen)
    monkeypatch.setattr(FakeOpen, "errors", {})
    monkeypatch.setattr(FakeOpen, "opened", [])
    monkeypatch.setattr(FakeConnection, "connect_error", None)
    monkeypatch.setattr(FakeSession, "connect_error", None)
    monkeypatch.setattr(FakeTree, "errors", {})
    monkeypatch.setattr(FakeTree, "connected", [])
    return monkeypatch


def test_smb_sign_in_accepts_and_requires_signing(fake_smb):
    assert smb_sign_in("nas", "alice", "pw", ["A", "B"]) == {"A", "B"}
    assert FakeTree.connected == [r"\\nas\A", r"\\nas\B"]
    # Only opens each share's top folder, for listing, and never creates anything.
    list_access = auth.DirectoryAccessMask.FILE_LIST_DIRECTORY | auth.DirectoryAccessMask.FILE_READ_ATTRIBUTES
    assert FakeOpen.opened == [(s, "", list_access, auth.CreateDisposition.FILE_OPEN) for s in "AB"]
    assert FakeConnection.created.require_signing is True
    assert FakeConnection.created.closed


def test_smb_sign_in_keeps_only_shares_samba_allows(fake_smb):
    fake_smb.setattr(FakeTree, "errors", {"A": AccessDenied(), "C": BadNetworkName()})
    fake_smb.setattr(FakeOpen, "errors", {"D": AccessDenied()})  # connects, but can't read the folder
    assert smb_sign_in("nas", "alice", "pw", ["A", "B", "C", "D"]) == {"B"}
    assert FakeConnection.created.closed


def test_smb_sign_in_refuses_guest_and_logon_failures(fake_smb):
    fake_smb.setattr(FakeSession, "connect_error", SMBException("authenticated as a guest"))
    assert smb_sign_in("nas", "alice", "pw", ["A"]) is None
    assert FakeTree.connected == []
    assert FakeConnection.created.closed


@pytest.mark.parametrize("error", [ValueError("Failed to connect"), OSError("no route")])
def test_smb_sign_in_unreachable(fake_smb, error):
    fake_smb.setattr(FakeConnection, "connect_error", error)
    with pytest.raises(AuthUnavailable):
        smb_sign_in("nas", "alice", "pw", ["A"])


@pytest.mark.parametrize("stage", ["session", "tree"])
def test_smb_connection_lost_is_not_a_wrong_password(fake_smb, stage):
    if stage == "session":
        fake_smb.setattr(FakeSession, "connect_error", SMBConnectionClosed("closed"))
    else:
        fake_smb.setattr(FakeTree, "errors", {"A": SMBConnectionClosed("closed")})
    with pytest.raises(AuthUnavailable):
        smb_sign_in("nas", "alice", "pw", ["A"])
