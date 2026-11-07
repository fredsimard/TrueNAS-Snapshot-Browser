"""Sign-in (PLAN.md §7): a password is valid if the NAS's SMB server accepts it.

Samba already knows each server's accounts, local or Active Directory, so nothing about them is
configured or stored here. Passwords live only for the duration of the SMB call.

Signing is required. A session the server silently downgrades to guest can't be signed, so
smbprotocol refuses it, and a verified signature proves the server knew the password.

The same session then connects to each configured share and opens its top folder for listing, so
Samba decides which shares the account may use: valid users, share ACLs, folder permissions and the
rest of its rules apply unchanged. Connecting alone isn't enough: Samba accepts the connection for an
account that can't read the share's folder, then refuses every request on it. Nothing is listed, read
or written.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from smbprotocol.connection import Connection
from smbprotocol.exceptions import BadNetworkName, SMBConnectionClosed, SMBException
from smbprotocol.open import (
    CreateDisposition,
    CreateOptions,
    DirectoryAccessMask,
    FileAttributes,
    ImpersonationLevel,
    Open,
    ShareAccess,
)
from smbprotocol.session import Session
from smbprotocol.tree import TreeConnect

log = logging.getLogger(__name__)

SMB_PORT = 445
SMB_TIMEOUT_SECONDS = 10
SESSION_IDLE_SECONDS = 3600
THROTTLE_WINDOW_SECONDS = 300
MAX_FAILURES_PER_USER = 5  # below typical AD lockout thresholds, so typos can't lock an account
MAX_FAILURES_PER_ADDRESS = 20


class AuthUnavailable(Exception):
    """The SMB server couldn't be reached, so the password was neither accepted nor refused."""


# (username, password, share names) -> the shares the account may use, or None if the password
# is refused. Raises AuthUnavailable.
SignIn = Callable[[str, str, Sequence[str]], "Collection[str] | None"]


def smb_sign_in(host: str, username: str, password: str, shares: Sequence[str]) -> frozenset[str] | None:
    conn = Connection(uuid.uuid4(), host, SMB_PORT, require_signing=True)
    try:
        conn.connect(timeout=SMB_TIMEOUT_SECONDS)
    except (OSError, ValueError, SMBException) as e:
        raise AuthUnavailable(type(e).__name__) from None
    try:
        session = Session(conn, username, password, require_encryption=False, auth_protocol="ntlm")
        try:
            session.connect()
        except (OSError, SMBConnectionClosed) as e:
            raise AuthUnavailable(type(e).__name__) from None
        except Exception as e:  # logon failures, expired passwords, guest sessions, NTLM errors
            log.info("SMB refused %r: %s", username, type(e).__name__)
            return None
        return frozenset(s for s in shares if _can_use(session, host, s, username))
    finally:
        # Drop the connection without logging off: Samba ends the session and its shares with it, and
        # a share it refused also refuses the polite disconnect.
        conn.disconnect(close=False)


def _can_use(session: Session, host: str, share: str, username: str) -> bool:
    try:
        tree = TreeConnect(session, rf"\\{host}\{share}")
        tree.connect()
        top = Open(tree, "")
        top.create(
            ImpersonationLevel.Impersonation,
            DirectoryAccessMask.FILE_LIST_DIRECTORY | DirectoryAccessMask.FILE_READ_ATTRIBUTES,
            FileAttributes.FILE_ATTRIBUTE_DIRECTORY,
            ShareAccess.FILE_SHARE_READ | ShareAccess.FILE_SHARE_WRITE | ShareAccess.FILE_SHARE_DELETE,
            CreateDisposition.FILE_OPEN,
            CreateOptions.FILE_DIRECTORY_FILE,
        )
        top.close()
    except (OSError, SMBConnectionClosed) as e:
        raise AuthUnavailable(type(e).__name__) from None
    except BadNetworkName:
        log.warning("SMB share %r doesn't exist on the server; check that root's smb_share", share)
        return False
    except SMBException as e:
        log.info("SMB share %r refused for %r: %s", share, username, type(e).__name__)
        return False
    return True


class Closable(Protocol):
    def close(self) -> None: ...


@dataclass
class UserSession:
    username: str
    roots: frozenset[str]  # ids of the roots this user may browse
    expires: float
    workers: Closable | None = None  # reads files as this account (workers.Runner)
    downloads: dict = field(default_factory=dict)  # id → transfer.Download


class SessionStore:
    """In-memory sessions: a restart signs everyone out. Expiry slides with each request.

    A session's workers are closed when it ends: at sign-out, or the first time the store notices
    it has expired, which is at most PRUNE_SECONDS after it did while the app is in use.
    """

    PRUNE_SECONDS = 60.0

    def __init__(self, idle_seconds: float = SESSION_IDLE_SECONDS, clock=time.monotonic):
        self._idle = idle_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, UserSession] = {}
        self._next_prune = 0.0

    def create(self, username: str, roots: Collection[str], workers: Closable | None = None) -> str:
        token = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            ended = self._prune(now)
            self._sessions[token] = UserSession(username, frozenset(roots), now + self._idle, workers)
        _end(ended)
        return token

    def get(self, token: str | None) -> UserSession | None:
        now = self._clock()
        found = None
        with self._lock:
            ended = self._prune(now) if now >= self._next_prune else []
            s = self._sessions.get(token) if token else None
            if s is not None and s.expires <= now:
                ended.append(self._sessions.pop(token))
            elif s is not None:
                s.expires = now + self._idle
                found = s
        _end(ended)
        return found

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            s = self._sessions.pop(token, None)
        _end([s] if s else [])

    def _prune(self, now: float) -> list[UserSession]:
        self._next_prune = now + self.PRUNE_SECONDS
        ended = [t for t, s in self._sessions.items() if s.expires <= now]
        return [self._sessions.pop(t) for t in ended]


def _end(sessions: list[UserSession]) -> None:
    for s in sessions:
        if s.workers is not None:
            s.workers.close()


class LoginThrottle:
    """Blocks a key after too many failures within the window."""

    def __init__(self, max_failures: int, window_seconds: float = THROTTLE_WINDOW_SECONDS, clock=time.monotonic):
        self._max = max_failures
        self._window = window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}

    def blocked(self, key: str) -> bool:
        cutoff = self._clock() - self._window
        with self._lock:
            return sum(t > cutoff for t in self._failures.get(key, ())) >= self._max

    def failed(self, key: str) -> None:
        now = self._clock()
        cutoff = now - self._window
        with self._lock:
            self._failures = {
                k: kept for k, times in self._failures.items() if (kept := [t for t in times if t > cutoff])
            }
            self._failures.setdefault(key, []).append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
