"""HTTP API and static frontend.

Clients address content as root id + relative path + opaque point id. No absolute path,
snapshot name or OS error text is ever sent to the client. Everything except sign-in and the
server's name requires a session, and each session sees only the roots whose SMB share the
account could connect to at sign-in. Every file access goes to the session's workers, which run as
the signed-in account (workers.py); this process never reads a share.
"""

from __future__ import annotations

import logging
import os
import secrets
from functools import partial

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import (
    MAX_FAILURES_PER_ADDRESS,
    MAX_FAILURES_PER_USER,
    AuthUnavailable,
    LoginThrottle,
    SessionStore,
    SignIn,
    UserSession,
    smb_sign_in,
)
from .config import Config
from .ops import ERROR_STATUS, MAX_NAMES
from .transfer import MAX_RUNNING_PER_SESSION, Download, DownloadAborted, body, content_disposition, log_end, prune
from .workers import Backend, WorkerUnavailable

log = logging.getLogger(__name__)

SESSION_COOKIE = "snapback_session"


class LoginRequest(BaseModel):
    username: str = Field(max_length=256)
    password: str = Field(max_length=1024)


class DownloadRequest(BaseModel):
    path: str = Field("", max_length=8192)  # the folder the items are in
    at: str = Field("now", max_length=64)
    names: list[str] = Field(min_length=1, max_length=MAX_NAMES)


class SecurityHeaders:
    """Plain ASGI middleware: unlike @app.middleware, it doesn't sit between a streamed download and
    the connection."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        api = scope["path"].startswith("/api/")

        async def with_headers(message):
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                h["X-Content-Type-Options"] = "nosniff"
                h["Referrer-Policy"] = "no-referrer"
                h["X-Frame-Options"] = "DENY"
                if api:
                    h["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, with_headers)


class _DropAborted(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not (record.exc_info and isinstance(record.exc_info[1], DownloadAborted))


_drop_aborted = _DropAborted()


def create_app(
    config: Config,
    static_dir: str | None = None,
    verify: SignIn | None = None,
    *,
    backend: Backend,
) -> FastAPI:
    verify = verify or partial(smb_sign_in, config.smb_host)
    sessions = SessionStore()
    user_throttle = LoginThrottle(MAX_FAILURES_PER_USER)
    address_throttle = LoginThrottle(MAX_FAILURES_PER_ADDRESS)
    roots = {rc.id: rc for rc in config.roots}
    share_names = list(dict.fromkeys(rc.share_name for rc in config.roots))

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityHeaders)
    # An aborted download is reported in the audit log; the server's traceback for it is noise.
    if _drop_aborted not in logging.getLogger("uvicorn.error").filters:
        logging.getLogger("uvicorn.error").addFilter(_drop_aborted)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, __: RequestValidationError) -> JSONResponse:
        # The default handler echoes the submitted values, which can include a password.
        return JSONResponse({"error": "bad_request"}, status_code=400)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, DownloadAborted):
            log.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"error": "internal"}, status_code=500)

    def signed_in(request: Request) -> UserSession:
        session = sessions.get(request.cookies.get(SESSION_COOKIE))
        if session is None:
            raise HTTPException(401, "not_signed_in")
        return session

    def call(session: UserSession, req: dict) -> dict:
        """The worker's whole answer, which may hold an fd; raises for errors."""
        # A root the user may not browse is indistinguishable from one that doesn't exist.
        if req["root"] not in roots or req["root"] not in session.roots:
            raise HTTPException(404, "unknown_root")
        try:
            reply = session.workers.call(req)
        except WorkerUnavailable as e:
            log.error("request from %r failed: %s", session.username, e)
            raise HTTPException(503, "unavailable") from None
        if "error" in reply:
            if "fd" in reply:
                os.close(reply["fd"])
            code = str(reply["error"])
            raise HTTPException(ERROR_STATUS.get(code, 500), code if code in ERROR_STATUS else "internal")
        return reply

    def run(root_id: str, session: UserSession, **req) -> dict:
        reply = call(session, {**req, "root": root_id})
        if "fd" in reply:
            os.close(reply["fd"])
        return reply["ok"]

    def get_download(dl_id: str, session: UserSession) -> Download:
        d = session.downloads.get(dl_id)
        if d is None:
            raise HTTPException(404, "unknown_download")
        return d

    @app.get("/api/info")
    def info(request: Request) -> dict:
        session = sessions.get(request.cookies.get(SESSION_COOKIE))
        return {
            "server_label": config.server_label,
            "user": session.username if session else None,
            "roots": [{"id": r.id, "label": r.label} for r in roots.values() if session and r.id in session.roots],
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
        # The password is checked whether or not the account is allowed, so the answer never reveals
        # which usernames are on the list. Shares are only tried for allowed accounts.
        allowed = config.allowed_users is None or username.casefold() in config.allowed_users
        try:
            shares = verify(username, body.password, share_names if allowed else [])
        except AuthUnavailable as e:
            log.error("sign-in unavailable, SMB server unreachable: %s", e)
            raise HTTPException(503, "auth_unavailable") from None
        if shares is None:
            user_throttle.failed(user_key)
            address_throttle.failed(address)
            log.warning("sign-in failed for %r from %s", username, address)
            raise HTTPException(401, "bad_credentials")
        user_throttle.clear(user_key)
        if not allowed:
            log.warning("sign-in refused for %r from %s: not in ALLOWED_USERS", username, address)
            raise HTTPException(403, "not_allowed")
        try:
            workers = backend.start(username)
        except WorkerUnavailable as e:
            log.error("sign-in unavailable for %r: %s", username, e)
            raise HTTPException(503, "auth_unavailable") from None
        if workers is None:
            log.warning("sign-in refused for %r from %s: no such account on this server", username, address)
            raise HTTPException(403, "not_allowed")
        visible = frozenset(rc.id for rc in config.roots if rc.share_name in shares)
        log.info("signed in: %r from %s, roots: %s", username, address, ", ".join(sorted(visible)) or "none")
        token = sessions.create(username, visible, workers)
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", path="/")
        return {"user": username}

    @app.post("/api/logout")
    def logout(request: Request, response: Response) -> dict:
        sessions.delete(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="strict", path="/")
        return {}

    @app.get("/api/roots/{root_id}/points")
    def points(root_id: str, session: UserSession = Depends(signed_in)) -> dict:
        return run(root_id, session, op="points")

    @app.get("/api/roots/{root_id}/folder")
    def folder(root_id: str, path: str = "", at: str = "now", session: UserSession = Depends(signed_in)) -> dict:
        return run(root_id, session, op="folder", path=path, at=at)

    @app.get("/api/roots/{root_id}/history")
    def history(root_id: str, path: str, session: UserSession = Depends(signed_in)) -> dict:
        return run(root_id, session, op="history", path=path)

    @app.post("/api/roots/{root_id}/downloads")
    def prepare_download(root_id: str, body_: DownloadRequest, session: UserSession = Depends(signed_in)) -> dict:
        plan = run(root_id, session, op="plan", path=body_.path, at=body_.at, names=body_.names)
        if plan["too_large"]:
            return {"too_large": True, "limit": plan["limit"]}
        if plan["kind"] == "file":
            path = "/".join(p for p in (body_.path.strip("/"), body_.names[0]) if p)
            req = {"op": "file", "root": root_id, "path": path, "at": body_.at}
        else:
            req = {"op": "zip", "root": root_id, "path": body_.path, "at": body_.at, "names": body_.names}
        prune(session.downloads)
        d = Download(secrets.token_urlsafe(16), session.username, req, plan["kind"], plan["name"], plan["bytes"])
        session.downloads[d.id] = d
        keys = ("kind", "name", "bytes", "files", "folders", "limit")
        return {"id": d.id, "too_large": False, **{k: plan[k] for k in keys}}

    @app.get("/api/downloads/{dl_id}")
    def start_download(dl_id: str, request: Request) -> Response:
        # The page opens this by navigating to it, so any answer but the download itself would
        # replace the page. Errors are 204 No Content, which browsers ignore for a navigation;
        # the page learns what happened from /progress.
        nothing = Response(status_code=204)
        session = sessions.get(request.cookies.get(SESSION_COOKIE))
        d = session.downloads.get(dl_id) if session else None
        if d is None or d.state != "ready":
            return nothing
        if sum(x.state == "running" for x in session.downloads.values()) >= MAX_RUNNING_PER_SESSION:
            d.state = "failed"
            log_end(d, "too many at once")
            return nothing
        d.state = "running"
        try:
            reply = call(session, d.req)
        except HTTPException as e:
            d.state = "failed"
            log_end(d, str(e.detail))
            return nothing
        ok, fd, d.audit = reply["ok"], reply["fd"], reply.get("audit", {})
        d.name, d.total = ok["name"], ok["bytes"]
        headers = {"Content-Disposition": content_disposition(d.name)}
        if d.kind == "file":
            headers["Content-Length"] = str(d.total)
            return StreamingResponse(body(d, fd, d.total), headers=headers, media_type="application/octet-stream")
        return StreamingResponse(body(d, fd, None), headers=headers, media_type="application/zip")

    @app.get("/api/downloads/{dl_id}/progress")
    def download_progress(dl_id: str, session: UserSession = Depends(signed_in)) -> dict:
        d = get_download(dl_id, session)
        return {"state": d.state, "sent": d.sent, "total": d.total}

    @app.delete("/api/downloads/{dl_id}")
    def cancel_download(dl_id: str, session: UserSession = Depends(signed_in)) -> dict:
        d = get_download(dl_id, session)
        if not d.finished:
            d.state = "cancelled"  # a running one stops at its next chunk
        return {"state": d.state}

    if static_dir and os.path.isfile(os.path.join(static_dir, "index.html")):
        mount_frontend(app, static_dir)

    return app


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
