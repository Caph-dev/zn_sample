"""FastAPI application factory for the localhost-only assistant."""
from __future__ import annotations

import secrets
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from assistant.api.health import router as health_router
from assistant.api.stores import router as stores_router
from assistant.security.csrf import is_local_host, is_valid_local_origin
from assistant.security.local_session import LocalSessionManager
from assistant.settings import SESSION_COOKIE
from assistant.web.routes import router as web_router


def application_version() -> str:
    try:
        return version("zn-sample")
    except PackageNotFoundError:
        return "dev"


def create_app(*, runtime_directory: Path, port: int) -> FastAPI:
    application = FastAPI(title="ZnSampleAssistant")
    session_manager = LocalSessionManager(runtime_directory)
    application.state.session_manager = session_manager
    application.state.port = port

    @application.middleware("http")
    async def enforce_local_security(request: Request, call_next):
        if not is_local_host(request.headers.get("host", "")):
            return JSONResponse({"detail": "invalid-host"}, status_code=400)

        public_path = request.url.path in {"/api/health", "/bootstrap"}
        static_path = request.url.path.startswith("/static/")
        session = session_manager.read_session(request.cookies.get(SESSION_COOKIE))
        if not public_path and not static_path and session is None:
            return JSONResponse({"detail": "session-required"}, status_code=401)
        request.state.session = session or {}

        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin", "")
            if not is_valid_local_origin(origin, port):
                return JSONResponse({"detail": "invalid-origin"}, status_code=403)
            submitted_token = request.headers.get("x-csrf-token", "")
            if not submitted_token:
                form = await request.form()
                submitted_token = str(form.get("csrf_token") or "")
            expected_token = str((session or {}).get("csrf") or "")
            if not expected_token or not secrets.compare_digest(
                submitted_token, expected_token
            ):
                return JSONResponse({"detail": "invalid-csrf"}, status_code=403)
        return await call_next(request)

    @application.get("/bootstrap")
    def bootstrap(token: str = ""):
        session_cookie = session_manager.consume_bootstrap_token(token)
        if not session_cookie:
            return JSONResponse({"detail": "invalid-bootstrap-token"}, status_code=401)
        response = RedirectResponse("/", status_code=302)
        response.set_cookie(
            SESSION_COOKIE,
            session_cookie,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    static_directory = Path(__file__).resolve().parent / "web" / "static"
    application.mount("/static", StaticFiles(directory=static_directory), name="static")
    application.include_router(health_router)
    application.include_router(stores_router)
    application.include_router(web_router)
    return application
