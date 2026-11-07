"""Privilege-dropping workers (PLAN.md §8): the kernel, not this app, decides what a user may read.

`python -m snapback` starts as root and splits into three kinds of process:

    supervisor  root. Reads the host's accounts and forks workers. Never parses HTTP, never reads
                a share.
    web         nobody. HTTP, sign-in and sessions. Asks the supervisor for workers by username,
                and only after Samba has accepted the password.
    worker      one signed-in account's uid and groups. Answers its session's requests (ops.py).

For each request, the supervisor looks the username up, forks, and the child drops to that identity
(setgroups, setgid, setuid, then checks that root can't be regained) before it reads anything. The
web process gets the worker's end of a new socket pair. A worker exits when that socket closes: at
sign-out, when the session expires, or when the web process dies.

Messages on both channels are a 4-byte length and a JSON value. A worker's answer can carry one
open file descriptor (a file or a pipe to download), passed with SCM_RIGHTS; the runner returns it
as the answer's "fd", and the caller owns it.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import resource
import select
import signal
import socket
import struct
import threading
from collections.abc import Callable, Collection, Sequence
from typing import NoReturn, Protocol

from .identity import HostAccounts, Identity
from .ops import Ops
from .tls import HostTLS

log = logging.getLogger(__name__)

_HEADER = struct.Struct("!I")
MAX_MESSAGE_BYTES = 256 << 20
MAX_WORKERS = 64  # across all sessions
MAX_WORKERS_PER_SESSION = 3  # the browser asks for a folder, the points and a history at once
REQUEST_TIMEOUT_SECONDS = 120.0  # a file's history can automount many snapshots
SUPERVISOR_TIMEOUT_SECONDS = 10.0
POLL_SECONDS = 2.0
EXIT_ACCOUNTS_CHANGED = 3


class WorkerUnavailable(Exception):
    pass


class ProtocolError(Exception):
    pass


# Wire format


def send_msg(sock: socket.socket, obj: object, fd: int | None = None) -> None:
    data = json.dumps(obj, separators=(",", ":")).encode()
    frame = _HEADER.pack(len(data)) + data
    if fd is None:
        sock.sendall(frame)
        return
    sent = socket.send_fds(sock, [frame], [fd])
    if sent < len(frame):
        sock.sendall(frame[sent:])


def _recv_exact(sock: socket.socket, n: int, buf: bytes = b"") -> bytes | None:
    """n bytes, or None if the peer closed before sending any."""
    chunks = [buf] if buf else []
    got = len(buf)
    while got < n:
        chunk = sock.recv(min(n - got, 1 << 20))
        if not chunk:
            if got == 0:
                return None
            raise ProtocolError("connection closed mid-message")
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def recv_msg(sock: socket.socket, want_fd: bool = False) -> tuple[object, int | None]:
    """(message, received fd). The message is None if the peer closed the connection."""
    fd = None
    if want_fd:
        head, fds, _, _ = socket.recv_fds(sock, _HEADER.size, 1)
        fd = fds[0] if fds else None
        if not head:
            if fd is not None:
                os.close(fd)
            return None, None
        head = _recv_exact(sock, _HEADER.size, head)
    else:
        head = _recv_exact(sock, _HEADER.size)
        if head is None:
            return None, None
    try:
        (n,) = _HEADER.unpack(head)
        if n > MAX_MESSAGE_BYTES:
            raise ProtocolError(f"message of {n} bytes")
        data = _recv_exact(sock, n)
        if data is None and n:
            raise ProtocolError("connection closed mid-message")
        return json.loads(data or b"null"), fd
    except BaseException:
        if fd is not None:
            os.close(fd)
        raise


# Identity change


def become(uid: int, gid: int, groups: Sequence[int]) -> None:
    """Irreversibly switch this process to uid, gid and exactly these supplementary groups."""
    if uid == 0 or gid == 0:
        raise ValueError("refusing to run as root")
    os.setgroups(list(groups))
    os.setgid(gid)
    os.setuid(uid)
    if hasattr(os, "getresuid"):
        ok = os.getresuid() == (uid, uid, uid) and os.getresgid() == (gid, gid, gid)
    else:
        ok = (os.getuid(), os.geteuid(), os.getgid(), os.getegid()) == (uid, uid, gid, gid)
    if not ok or set(os.getgroups()) != set(groups):
        raise RuntimeError("privilege drop didn't take effect")
    try:
        os.setuid(0)
    except PermissionError:
        return
    raise RuntimeError("root could be regained after the privilege drop")


def drop_to(identity: Identity) -> None:
    become(identity.uid, identity.gid, identity.groups)


# Worker


def serve(sock: socket.socket, ops: Ops) -> None:
    """Answer requests until the web process closes the socket."""
    while True:
        req, _ = recv_msg(sock)
        if req is None:
            return
        reply = ops.handle(req) if isinstance(req, dict) else {"error": "bad_request"}
        fd = reply.pop("fd", None)
        try:
            send_msg(sock, reply, fd=fd)
        except (BrokenPipeError, ConnectionResetError):
            return  # the web process gave up on this request
        finally:
            if fd is not None:
                os.close(fd)


def _close_fds_except(keep: int) -> None:
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    top = soft if 0 < soft < (1 << 20) else 1 << 20
    os.closerange(3, keep)
    os.closerange(keep + 1, top)


def _worker_main(sock: socket.socket, identity: Identity, ops: Ops, drop: Callable[[Identity], None]) -> NoReturn:
    code = 1
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl-C stops the web process; its socket closes
        _close_fds_except(sock.fileno())  # above all the supervisor's channel
        drop(identity)
        serve(sock, ops)
        code = 0
    except BaseException:
        log.exception("worker for %r failed", identity.name)
    finally:
        os._exit(code)


# Supervisor


def supervise(
    ctl: socket.socket,
    ops: Ops,
    accounts: HostAccounts,
    web_pid: int | None = None,
    drop: Callable[[Identity], None] = drop_to,
    tls: HostTLS | None = None,
) -> int:
    """Fork workers on request until the web process goes away, or the host's accounts or the
    certificate change.

    Returns the exit code for the container.
    """
    workers: set[int] = set()
    while True:
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break
            if pid == web_pid:
                log.error("the web process exited")
                return 1
            workers.discard(pid)
        if accounts.replaced():
            log.warning("the host's accounts changed; exiting so the app restarts and reads them")
            return EXIT_ACCOUNTS_CHANGED
        if tls is not None and tls.changed():
            log.warning("the TLS certificate changed; exiting so the app restarts and loads it")
            return EXIT_ACCOUNTS_CHANGED
        ready, _, _ = select.select([ctl], [], [], POLL_SECONDS)
        if not ready:
            continue
        req, _ = recv_msg(ctl)
        if req is None:
            log.error("the web process closed its channel")
            return 1
        req_id = req.get("id") if isinstance(req, dict) else None
        username = req.get("username") if isinstance(req, dict) else None
        if not isinstance(username, str) or not 0 < len(username) <= 256:
            send_msg(ctl, {"id": req_id, "error": "bad_request"})
            continue
        if accounts.replaced():
            continue  # never act on accounts that are out of date; the next loop exits
        identity = accounts.lookup(username)
        if identity is None:
            send_msg(ctl, {"id": req_id, "error": "no_account"})
            continue
        if len(workers) >= MAX_WORKERS:
            log.warning("worker limit (%d) reached", MAX_WORKERS)
            send_msg(ctl, {"id": req_id, "error": "busy"})
            continue
        ours, theirs = socket.socketpair()
        pid = os.fork()
        if pid == 0:
            ours.close()
            _worker_main(theirs, identity, ops, drop)
        theirs.close()
        workers.add(pid)
        log.info("worker %d for %r: uid %d, groups %s", pid, identity.name, identity.uid,
                 ",".join(map(str, identity.groups)))
        try:
            send_msg(ctl, {"id": req_id, "ok": True}, fd=ours.fileno())
        finally:
            ours.close()


# Web side


class Runner(Protocol):
    """One session's access to the files, as that session's account."""

    def call(self, req: dict) -> dict: ...

    def close(self) -> None: ...


class Backend(Protocol):
    def start(self, username: str) -> Runner | None:
        """A runner for this account, or None if the host has no such account."""
        ...


class SupervisedBackend:
    def __init__(self, ctl: socket.socket):
        self._ctl = ctl
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    def spawn(self, username: str) -> socket.socket | None:
        """A new worker for username, or None if the host has no such account."""
        with self._lock:
            req_id = next(self._ids)
            try:
                self._ctl.settimeout(SUPERVISOR_TIMEOUT_SECONDS)
                send_msg(self._ctl, {"id": req_id, "username": username})
                while True:
                    reply, fd = recv_msg(self._ctl, want_fd=True)
                    if not isinstance(reply, dict):
                        raise WorkerUnavailable("supervisor closed its channel")
                    if reply.get("id") == req_id:
                        break
                    if fd is not None:  # the answer to a request that timed out
                        os.close(fd)
            except (OSError, ProtocolError, ValueError) as e:
                raise WorkerUnavailable(f"supervisor: {type(e).__name__}") from None
        if reply.get("ok") and fd is not None:
            return socket.socket(fileno=fd)
        if fd is not None:
            os.close(fd)
        if reply.get("error") == "no_account":
            return None
        raise WorkerUnavailable(f"supervisor: {reply.get('error')}")

    def start(self, username: str) -> Runner | None:
        sock = self.spawn(username)
        if sock is None:
            return None
        return WorkerPool(lambda: self.spawn(username), sock)


class WorkerPool:
    """Up to MAX_WORKERS_PER_SESSION workers for one session, started as requests need them."""

    def __init__(self, spawn: Callable[[], socket.socket | None], first: socket.socket):
        self._spawn = spawn
        self._idle = [first]
        self._count = 1
        self._closed = False
        self._cond = threading.Condition()

    def call(self, req: dict) -> dict:
        sock = self._acquire()
        try:
            sock.settimeout(REQUEST_TIMEOUT_SECONDS)
            send_msg(sock, req)
            reply, fd = recv_msg(sock, want_fd=True)
        except (OSError, ProtocolError, ValueError) as e:
            self._discard(sock)
            raise WorkerUnavailable(f"worker: {type(e).__name__}") from None
        except BaseException:
            self._discard(sock)
            raise
        if not isinstance(reply, dict):
            if fd is not None:
                os.close(fd)
            self._discard(sock)
            raise WorkerUnavailable("worker exited")
        self._release(sock)
        if fd is not None:
            reply["fd"] = fd
        return reply

    def _acquire(self) -> socket.socket:
        with self._cond:
            while True:
                if self._closed:
                    raise WorkerUnavailable("session ended")
                if self._idle:
                    return self._idle.pop()
                if self._count < MAX_WORKERS_PER_SESSION:
                    self._count += 1
                    break
                if not self._cond.wait(REQUEST_TIMEOUT_SECONDS):
                    raise WorkerUnavailable("all of this session's workers are busy")
        try:
            sock = self._spawn()
        except BaseException:
            self._forget()
            raise
        if sock is None:
            self._forget()
            raise WorkerUnavailable("account no longer exists")
        return sock

    def _forget(self) -> None:
        with self._cond:
            self._count -= 1
            self._cond.notify()

    def _release(self, sock: socket.socket) -> None:
        with self._cond:
            if self._closed:
                sock.close()
                self._count -= 1
            else:
                self._idle.append(sock)
            self._cond.notify()

    def _discard(self, sock: socket.socket) -> None:
        sock.close()
        self._forget()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            for s in self._idle:
                s.close()
            self._count -= len(self._idle)
            self._idle.clear()
            self._cond.notify_all()


class InProcessBackend:
    """Runs requests in this process, as whoever runs it, with no privilege drop.

    For tests and scripts/dev_server.py only; `python -m snapback` never uses it. `accounts`, if
    given, plays the host's account list.
    """

    def __init__(self, ops: Ops, accounts: Collection[str] | None = None):
        self._ops = ops
        self._accounts = accounts

    def start(self, username: str) -> Runner | None:
        if self._accounts is not None and username not in self._accounts:
            return None
        return _InProcessRunner(self._ops)


class _InProcessRunner:
    def __init__(self, ops: Ops):
        self._ops = ops
        self.closed = False

    def call(self, req: dict) -> dict:
        if self.closed:
            raise WorkerUnavailable("session ended")
        # Through JSON, like the real thing.
        reply = self._ops.handle(json.loads(json.dumps(req)))
        fd = reply.pop("fd", None)
        reply = json.loads(json.dumps(reply))
        if fd is not None:
            reply["fd"] = fd
        return reply

    def close(self) -> None:
        self.closed = True
