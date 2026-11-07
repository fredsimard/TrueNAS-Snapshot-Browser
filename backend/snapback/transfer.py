"""Downloads, web side: sending what a worker opened, progress, cancelling, and the audit log.

A download is prepared first (size and name, for the confirm dialog), then fetched once by the
browser as an ordinary download, so a 20 GB zip never sits in browser memory. The page follows its
progress and can cancel it. A download that doesn't finish properly (cancelled, a zip that couldn't
be completed, a file that shrank while it was read) ends with a dropped connection, so the browser
marks it failed instead of saving a damaged copy as if it were whole.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from urllib.parse import quote

from .downloads import ZIP_END_SIZE, zip_complete

audit = logging.getLogger("snapback.audit")

CHUNK = 1 << 20
MAX_RUNNING_PER_SESSION = 3
KEEP_SECONDS = 3600  # finished downloads stay visible to the page this long
READY_SECONDS = 600  # a prepared download must start within this


class DownloadAborted(Exception):
    """Raised mid-response: the server drops the connection and the browser marks the download failed."""


@dataclass
class Download:
    id: str
    username: str
    req: dict  # the worker request that opens it
    kind: str  # "file" | "zip"
    name: str
    total: int  # bytes of file contents
    created: float = field(default_factory=time.monotonic)
    state: str = "ready"  # ready → running → done | failed | cancelled
    sent: int = 0
    audit: dict = field(default_factory=dict)

    @property
    def finished(self) -> bool:
        return self.state in ("done", "failed", "cancelled")


def prune(downloads: dict[str, Download], now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    for k in [k for k, d in downloads.items() if
              (d.finished and now - d.created > KEEP_SECONDS) or (d.state == "ready" and now - d.created > READY_SECONDS)]:
        del downloads[k]


def content_disposition(name: str) -> str:
    fallback = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in name)
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def body(d: Download, fd: int, length: int | None, read: Callable[[int, int], bytes] = os.read) -> Iterator[bytes]:
    """The response body: reads fd until EOF (a zip) or length bytes (a file), tracking progress."""
    tail = b""
    try:
        while True:
            if d.state == "cancelled":
                raise DownloadAborted("cancelled")
            want = CHUNK if length is None else min(CHUNK, length - d.sent)
            if want <= 0:
                break
            chunk = read(fd, want)
            if not chunk:
                break
            d.sent += len(chunk)
            if length is None:
                tail = (tail + chunk)[-ZIP_END_SIZE:]
            yield chunk
        if length is None and not zip_complete(tail):
            raise DownloadAborted("zip not completed")
        if length is not None and d.sent < length:
            raise DownloadAborted("file shrank while it was read")
        d.state = "done"
    except GeneratorExit:
        d.state = "cancelled"  # the browser stopped it
        raise
    except BaseException:
        if d.state != "cancelled":
            d.state = "failed"
        raise
    finally:
        os.close(fd)
        log_end(d)


def log_end(d: Download, reason: str = "") -> None:
    a = d.audit
    what = a.get("path", "?") + (f": {a['items']}" if a.get("items") else "")
    sent = f"{d.sent} of {d.total} bytes" if d.kind == "file" else f"{d.sent} bytes sent, {d.total} bytes of files"
    audit.info(
        "download %s by %r: %s %s as of %s, %s%s",
        d.state, d.username, d.kind, what, a.get("version", "?"), sent, f" ({reason})" if reason else "",
    )
