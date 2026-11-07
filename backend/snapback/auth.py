"""Sign-in (PLAN.md §7): a password is valid if the NAS's SMB server accepts it.

Samba already knows each server's accounts, local or Active Directory, so nothing about them is
configured or stored here. Passwords live only for the duration of the SMB call.

Signing is required. A session the server silently downgrades to guest can't be signed, so
smbprotocol refuses it, and a verified signature proves the server knew the password.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
import uuid
from dataclasses import dataclass

from smbprotocol.connection import Connection
from smbprotocol.exceptions import SMBConnectionClosed, SMBException
from smbprotocol.session import Session

log = logging.getLogger(__name__)

SMB_PORT = 445
SMB_TIMEOUT_SECONDS = 10
SESSION_IDLE_SECONDS = 3600
THROTTLE_WINDOW_SECONDS = 300
MAX_FAILURES_PER_USER = 5  # below typical AD lockout thresholds, so typos can't lock an account
MAX_FAILURES_PER_ADDRESS = 20


class AuthUnavailable(Exception):
    """The SMB server couldn't be reached, so the password was neither accepted nor refused."""


def smb_verify(host: str, username: str, password: str) -> bool:
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
            return False
        session.disconnect()
        return True
    finally:
        conn.disconnect()


@dataclass
class _Session:
    username: str
    expires: float


class SessionStore:
    """In-memory sessions: a restart signs everyone out. Expiry slides with each request."""

    def __init__(self, idle_seconds: float = SESSION_IDLE_SECONDS, clock=time.monotonic):
        self._idle = idle_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, _Session] = {}

    def create(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            self._sessions = {t: s for t, s in self._sessions.items() if s.expires > now}
            self._sessions[token] = _Session(username, now + self._idle)
        return token

    def get(self, token: str | None) -> str | None:
        if not token:
            return None
        now = self._clock()
        with self._lock:
            s = self._sessions.get(token)
            if s is None:
                return None
            if s.expires <= now:
                del self._sessions[token]
                return None
            s.expires = now + self._idle
            return s.username

    def delete(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._sessions.pop(token, None)


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
