"""Supervisor, workers and the protocol between them, with real processes and sockets.

The privilege drop itself needs root: here it's replaced by a function that records the identity, and
test_become_for_real runs only as root (e.g. in the container).
"""

import os
import socket
import threading
import time

import pytest

from snapback import workers
from snapback.identity import HostAccounts
from snapback.workers import (
    EXIT_ACCOUNTS_CHANGED,
    MAX_WORKERS_PER_SESSION,
    ProtocolError,
    SupervisedBackend,
    WorkerUnavailable,
    become,
    recv_msg,
    send_msg,
    supervise,
)

PASSWD = "alice:x:3000:3000::/:/bin/sh\nbob:x:3001:3001::/:/bin/sh\n"
GROUP = "staff:x:545:alice\n"

WHO = None  # set in the worker by fake_drop


def fake_drop(identity):
    global WHO
    WHO = [identity.name, identity.uid, list(identity.groups)]


class EchoOps:
    """Stands in for ops.Ops: reports which process answered, as whom, and what it holds open."""

    def handle(self, req):
        if req.get("op") == "sleep":
            time.sleep(req["seconds"])
        if req.get("op") == "exit":
            os._exit(0)
        if req.get("op") == "pipe":
            r, w = os.pipe()
            os.write(w, b"from the worker")
            os.close(w)
            return {"ok": {}, "fd": r}
        open_fds = []
        for fd in range(3, 256):
            try:
                os.fstat(fd)
                open_fds.append(fd)
            except OSError:
                pass
        return {"ok": {"req": req, "pid": os.getpid(), "who": WHO, "fds": open_fds}}


@pytest.fixture
def supervisor(tmp_path, monkeypatch):
    """A forked supervisor; yields (backend, its pid, the host passwd path)."""
    monkeypatch.setattr(workers, "POLL_SECONDS", 0.05)
    passwd, group = tmp_path / "passwd", tmp_path / "group"
    passwd.write_text(PASSWD)
    group.write_text(GROUP)
    accounts = HostAccounts(str(passwd), str(group))
    web_end, sup_end = socket.socketpair()
    pid = os.fork()
    if pid == 0:
        code = 99
        try:
            web_end.close()
            code = supervise(sup_end, EchoOps(), accounts, drop=fake_drop)
        finally:
            os._exit(code)
    sup_end.close()
    accounts.close()
    state = {"pid": pid, "sup_fd": sup_end.fileno(), "passwd": passwd, "web_end": web_end}
    yield SupervisedBackend(web_end), state
    web_end.close()
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass


def exit_code(pid, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            return os.waitstatus_to_exitcode(status)
        time.sleep(0.02)
    raise AssertionError("supervisor didn't exit")


def test_messages_round_trip():
    a, b = socket.socketpair()
    send_msg(a, {"x": [1, "é"]})
    assert recv_msg(b) == ({"x": [1, "é"]}, None)
    a.close()
    assert recv_msg(b) == (None, None)


def test_a_descriptor_travels_with_a_message():
    a, b = socket.socketpair()
    r, w = os.pipe()
    send_msg(a, {"ok": True}, fd=r)
    msg, fd = recv_msg(b, want_fd=True)
    assert msg == {"ok": True}
    os.write(w, b"hi")
    assert os.read(fd, 2) == b"hi"


def test_oversized_message_is_refused(monkeypatch):
    monkeypatch.setattr(workers, "MAX_MESSAGE_BYTES", 10)
    a, b = socket.socketpair()
    send_msg(a, "x" * 20)
    with pytest.raises(ProtocolError):
        recv_msg(b)


def test_become_refuses_root():
    with pytest.raises(ValueError):
        become(0, 3000, [3000])
    with pytest.raises(ValueError):
        become(3000, 0, [3000])


@pytest.mark.skipif(os.geteuid() != 0, reason="needs root")
def test_become_for_real():
    pid = os.fork()
    if pid == 0:
        code = 1
        try:
            become(65534, 65534, [65534, 545])
            ok = os.getuid() == os.geteuid() == 65534 and set(os.getgroups()) == {65534, 545}
            code = 0 if ok else 2
        finally:
            os._exit(code)
    assert os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]) == 0


def test_worker_runs_as_the_account(supervisor):
    backend, _ = supervisor
    runner = backend.start("alice")
    reply = runner.call({"op": "hello"})
    assert reply["ok"]["req"] == {"op": "hello"}
    assert reply["ok"]["who"] == ["alice", 3000, [545, 3000]]
    runner.close()


def test_worker_holds_only_its_own_socket(supervisor):
    backend, state = supervisor
    reply = backend.start("alice").call({"op": "fds"})
    assert len(reply["ok"]["fds"]) == 1  # not the supervisor's channel, not the account files


def test_unknown_account_gets_no_worker(supervisor):
    backend, _ = supervisor
    assert backend.start("mallory") is None
    assert backend.start("bob").call({})["ok"]["who"][0] == "bob"  # the channel still works


def test_session_starts_more_workers_when_busy(supervisor):
    backend, _ = supervisor
    runner = backend.start("alice")
    pids, threads = [], []
    for _ in range(MAX_WORKERS_PER_SESSION + 1):
        t = threading.Thread(target=lambda: pids.append(runner.call({"op": "sleep", "seconds": 0.3})["ok"]["pid"]))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    assert len(pids) == MAX_WORKERS_PER_SESSION + 1
    assert len(set(pids)) == MAX_WORKERS_PER_SESSION  # the extra request waited for one of them


def test_workers_exit_when_the_session_ends(supervisor):
    backend, _ = supervisor
    runner = backend.start("alice")
    pid = runner.call({})["ok"]["pid"]
    runner.close()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)  # until the supervisor has reaped it
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("worker still running")
    with pytest.raises(WorkerUnavailable):
        runner.call({})


def test_a_dead_worker_is_replaced(supervisor):
    backend, _ = supervisor
    runner = backend.start("alice")
    first = runner.call({})["ok"]["pid"]
    with pytest.raises(WorkerUnavailable):
        runner.call({"op": "exit"})
    assert runner.call({})["ok"]["pid"] != first


def test_replaced_accounts_stop_the_supervisor(supervisor):
    backend, state = supervisor
    backend.start("alice").call({})
    new = state["passwd"].with_suffix(".new")
    new.write_text(PASSWD)
    os.replace(new, state["passwd"])
    assert exit_code(state["pid"]) == EXIT_ACCOUNTS_CHANGED


def test_supervisor_stops_when_the_web_process_goes(supervisor):
    _, state = supervisor
    state["web_end"].close()
    assert exit_code(state["pid"]) == 1


def test_supervisor_gone(supervisor):
    backend, state = supervisor
    os.kill(state["pid"], 9)
    os.waitpid(state["pid"], 0)
    with pytest.raises(WorkerUnavailable):
        backend.start("alice")


def test_a_workers_descriptor_reaches_the_web_process(supervisor):
    backend, _ = supervisor
    reply = backend.start("alice").call({"op": "pipe"})
    assert os.read(reply["fd"], 100) == b"from the worker"
    os.close(reply["fd"])


class ChangedTLS:
    def changed(self):
        return True


def test_a_new_certificate_stops_the_supervisor(tmp_path, monkeypatch):
    monkeypatch.setattr(workers, "POLL_SECONDS", 0.05)
    (tmp_path / "passwd").write_text(PASSWD)
    (tmp_path / "group").write_text(GROUP)
    accounts = HostAccounts(str(tmp_path / "passwd"), str(tmp_path / "group"))
    web_end, sup_end = socket.socketpair()
    assert supervise(sup_end, EchoOps(), accounts, drop=fake_drop, tls=ChangedTLS()) == EXIT_ACCOUNTS_CHANGED
