from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.build_manager import BuildManager
from app.config import Settings
from app.request_limits import BuildUploadLimitMiddleware, RequestBodyTooLarge
from app.routes.api import router as api_router
from app.routes.web import router as web_router
from app.security import SessionCookies


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    templates_dir = settings.project_root / "app/templates"
    static_dir = settings.project_root / "app/static"

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if not settings.admin_password:
            raise RuntimeError("ADMIN_PASSWORD must be set before starting the private APK builder.")
        manager = BuildManager(settings)
        application.state.build_manager = manager
        manager.start()
        try:
            yield
        finally:
            await manager.close()

    application = FastAPI(title="Private APK Builder", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    application.state.settings = settings
    application.state.sessions = SessionCookies(settings.session_secret)
    application.state.templates = Jinja2Templates(directory=str(templates_dir))
    application.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    application.include_router(web_router)
    application.include_router(api_router)
    application.add_middleware(
        BuildUploadLimitMiddleware,
        max_bytes=settings.max_upload_mb * 1024 * 1024,
    )

    @application.exception_handler(RequestBodyTooLarge)
    async def request_body_too_large(_request: Request, _exc: RequestBodyTooLarge):
        return JSONResponse({"detail": "The upload exceeds the configured size limit."}, status_code=413)

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path == "/dashboard" or request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "private, no-store")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
        )
        return response

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ["PORT"]),
                proxy_headers=True, forwarded_allow_ips="*")
