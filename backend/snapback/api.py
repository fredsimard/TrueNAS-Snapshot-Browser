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
    SignIn,
    UserSession,
    smb_sign_in,
)
from .config import Config
from .ops import ERROR_STATUS
from .workers import Backend, WorkerUnavailable

log = logging.getLogger(__name__)

SESSION_COOKIE = "snapback_session"


class LoginRequest(BaseModel):
    username: str = Field(max_length=256)
    password: str = Field(max_length=1024)


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

    def signed_in(request: Request) -> UserSession:
        session = sessions.get(request.cookies.get(SESSION_COOKIE))
        if session is None:
            raise HTTPException(401, "not_signed_in")
        return session

    def run(root_id: str, session: UserSession, **req: str) -> dict:
        # A root the user may not browse is indistinguishable from one that doesn't exist.
        if root_id not in roots or root_id not in session.roots:
            raise HTTPException(404, "unknown_root")
        try:
            reply = session.workers.call({**req, "root": root_id})
        except WorkerUnavailable as e:
            log.error("request from %r failed: %s", session.username, e)
            raise HTTPException(503, "unavailable") from None
        if "error" in reply:
            code = str(reply["error"])
            raise HTTPException(ERROR_STATUS.get(code, 500), code if code in ERROR_STATUS else "internal")
        return reply["ok"]

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
