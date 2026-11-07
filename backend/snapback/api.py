"""HTTP API and static frontend.

Clients address content as root id + relative path + opaque point id. No absolute path,
snapshot name or OS error text is ever sent to the client. Everything except sign-in and the
server's name requires a session.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from functools import partial

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import (
    MAX_FAILURES_PER_ADDRESS,
    MAX_FAILURES_PER_USER,
    AuthUnavailable,
    LoginThrottle,
    SessionStore,
    smb_verify,
)
from .browse import FolderResult, History, Row, Timeline, file_history, list_folder
from .config import Config
from .paths import Entry, PathRejected, Root, parse_rel, resolve_root
from .snapshots import SnapshotIndex

log = logging.getLogger(__name__)

SESSION_COOKIE = "snapback_session"


class LoginRequest(BaseModel):
    username: str = Field(max_length=256)
    password: str = Field(max_length=1024)


def create_app(
    config: Config,
    static_dir: str | None = None,
    verify: Callable[[str, str], bool] | None = None,
) -> FastAPI:
    verify = verify or partial(smb_verify, config.smb_host)
    sessions = SessionStore()
    user_throttle = LoginThrottle(MAX_FAILURES_PER_USER)
    address_throttle = LoginThrottle(MAX_FAILURES_PER_ADDRESS)
    roots: dict[str, Root] = {rc.id: resolve_root(rc) for rc in config.roots}
    index = SnapshotIndex(
        prefixes=config.snapshot_prefixes,
        max_listed=config.max_snapshots_listed,
        ttl_seconds=config.snapshot_cache_seconds,
        tz=config.timezone,
    )
    for r in roots.values():
        log.info(
            "root %s: share_root=%s dataset_mountpoint=%s relative_prefix=%s snapshots=%d",
            r.id, r.share_root, r.dataset_mountpoint, "/".join(r.relative_prefix) or ".",
            len(index.list(r.snapshot_dir)),
        )

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, __: RequestValidationError) -> JSONResponse:
        # The default handler echoes the submitted values, which can include a password.
        return JSONResponse({"error": "bad_request"}, status_code=400)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"error": "internal"}, status_code=500)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def signed_in(request: Request) -> str:
        user = sessions.get(request.cookies.get(SESSION_COOKIE))
        if user is None:
            raise HTTPException(401, "not_signed_in")
        return user

    def get_root(root_id: str) -> Root:
        root = roots.get(root_id)
        if root is None:
            raise HTTPException(404, "unknown_root")
        return root

    def get_parts(path: str) -> tuple[str, ...]:
        try:
            return parse_rel(path)
        except PathRejected:
            raise HTTPException(400, "bad_path") from None

    def timeline(root: Root) -> Timeline:
        return Timeline(root, index.list(root.snapshot_dir))

    def point_index(tl: Timeline, at: str) -> int:
        i = tl.index_of(at)
        if i is None:
            raise HTTPException(404, "unknown_point")
        return i

    @app.get("/api/info")
    def info(request: Request) -> dict:
        user = sessions.get(request.cookies.get(SESSION_COOKIE))
        return {
            "server_label": config.server_label,
            "user": user,
            "roots": [{"id": r.id, "label": r.label} for r in roots.values()] if user else [],
        }

    @app.post("/api/login")
    def login(body: LoginRequest, request: Request, response: Response) -> dict:
        username = body.username.strip()
        address = request.client.host if request.client else "unknown"
        user_key = username.lower()
        if not username or not body.password:
            raise HTTPException(401, "bad_credentials")
        if user_throttle.blocked(user_key) or address_throttle.blocked(address):
            log.warning("sign-in blocked for %r from %s: too many failures", username, address)
            raise HTTPException(429, "too_many_attempts")
        try:
            ok = verify(username, body.password)
        except AuthUnavailable as e:
            log.error("sign-in unavailable, SMB server unreachable: %s", e)
            raise HTTPException(503, "auth_unavailable") from None
        if not ok:
            user_throttle.failed(user_key)
            address_throttle.failed(address)
            log.warning("sign-in failed for %r from %s", username, address)
            raise HTTPException(401, "bad_credentials")
        user_throttle.clear(user_key)
        log.info("signed in: %r from %s", username, address)
        response.set_cookie(SESSION_COOKIE, sessions.create(username), httponly=True, samesite="strict", path="/")
        return {"user": username}

    @app.post("/api/logout")
    def logout(request: Request, response: Response) -> dict:
        sessions.delete(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="strict", path="/")
        return {}

    @app.get("/api/roots/{root_id}/points", dependencies=[Depends(signed_in)])
    def points(root_id: str) -> dict:
        tl = timeline(get_root(root_id))
        return {"points": [{"id": s.id, "time": s.time.isoformat()} for s in tl.snapshots]}

    @app.get("/api/roots/{root_id}/folder", dependencies=[Depends(signed_in)])
    def folder(root_id: str, path: str = "", at: str = "now") -> dict:
        root = get_root(root_id)
        parts = get_parts(path)
        tl = timeline(root)
        return folder_json(tl, list_folder(tl, parts, point_index(tl, at)))

    @app.get("/api/roots/{root_id}/history", dependencies=[Depends(signed_in)])
    def history(root_id: str, path: str) -> dict:
        root = get_root(root_id)
        parts = get_parts(path)
        if not parts:
            raise HTTPException(400, "bad_path")
        tl = timeline(root)
        return history_json(tl, file_history(tl, parts))

    if static_dir and os.path.isfile(os.path.join(static_dir, "index.html")):
        mount_frontend(app, static_dir)

    return app


def entry_json(e: Entry) -> dict:
    return {"dir": e.is_dir, "size": e.size, "mtime": e.mtime_ns // 1_000_000}


def row_json(tl: Timeline, r: Row) -> dict:
    return {
        "name": r.entry.name,
        **entry_json(r.entry),
        "status": r.status,
        "now": entry_json(r.now) if r.now is not None else None,
        "last_seen": tl.point_id(r.last_seen) if r.last_seen is not None else None,
    }


def folder_json(tl: Timeline, res: FolderResult) -> dict:
    return {
        "state": res.state,
        "entries": [row_json(tl, r) for r in res.rows],
        "nearest": (
            {"at": tl.point_id(res.nearest), "direction": res.direction}
            if res.nearest is not None
            else None
        ),
    }


def history_json(tl: Timeline, h: History) -> dict:
    return {
        "now": entry_json(h.now) if h.now is not None else None,
        "versions": [
            {**entry_json(v.entry), "current": v.current, "points": [tl.point_id(i) for i in v.points]}
            for v in h.versions
        ],
    }


def mount_frontend(app: FastAPI, static_dir: str) -> None:
    index_html = os.path.join(static_dir, "index.html")
    assets = os.path.join(static_dir, "assets")
    if os.path.isdir(assets):
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    top_files = {n for n in os.listdir(static_dir) if os.path.isfile(os.path.join(static_dir, n))}

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(404, "not_found")
        if full_path in top_files:
            return FileResponse(os.path.join(static_dir, full_path))
        return FileResponse(index_html, headers={"Cache-Control": "no-cache"})
