"""FastAPI application factory for the localhost-only assistant."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from assistant.api.health import router as health_router
from assistant.api.stores import router as stores_router
from assistant.security.csrf import is_local_host, is_valid_local_origin
from assistant.web.routes import router as web_router


def application_version() -> str:
    from assistant.paths import _release_manifest

    release = _release_manifest()
    if release is not None:
        application_version_value = release[1].get("application_version")
        if not isinstance(application_version_value, str) or not application_version_value or application_version_value == "dev":
            raise RuntimeError("release-version-required")
        return application_version_value
    try:
        return version("zn-sample")
    except PackageNotFoundError:
        return "dev"


def create_app(*, port: int) -> FastAPI:
    application = FastAPI(title="ZnSampleAssistant")
    application.state.port = port

    @application.middleware("http")
    async def enforce_local_security(request: Request, call_next):
        # Only the loopback host answer: a request arriving with any other
        # Host header (DNS rebinding from a malicious page) is dropped.
        if not is_local_host(request.headers.get("host", "")):
            return JSONResponse({"detail": "invalid-host"}, status_code=400)

        # Stateless CSRF gate for state-changing requests: browsers attach an
        # Origin header to same-origin form/fetch POSTs, and a cross-origin
        # attacker-triggered form always carries a foreign origin. Requests
        # without an Origin header (non-browser clients) are still checked:
        # the header never matches the local origin contract.
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin", "")
            if not is_valid_local_origin(origin, port):
                return JSONResponse({"detail": "invalid-origin"}, status_code=403)
        return await call_next(request)

    static_directory = Path(__file__).resolve().parent / "web" / "static"
    application.mount("/static", StaticFiles(directory=static_directory), name="static")
    application.include_router(health_router)
    application.include_router(stores_router)
    application.include_router(web_router)
    return application
