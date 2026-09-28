from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.auth import password_matches
from app.security import SessionCookies

router = APIRouter()
SESSION_COOKIE = "apk_builder_session"
SESSION_AGE_SECONDS = 12 * 60 * 60


def _secure_cookie(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    if request.app.state.sessions.verify(request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse("/dashboard", status_code=303)
    return request.app.state.templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form(...)) -> Response:
    settings = request.app.state.settings
    if password_matches(password, settings.admin_password):
        token, _csrf = request.app.state.sessions.create()
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_AGE_SECONDS,
            httponly=True,
            secure=_secure_cookie(request),
            samesite="strict",
            path="/",
        )
        return response
    return request.app.state.templates.TemplateResponse(
        request, "login.html", {"error": "That password was not accepted."}, status_code=401
    )


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    csrf = request.app.state.sessions.verify(token)
    if csrf is None:
        return RedirectResponse("/login", status_code=303)
    return request.app.state.templates.TemplateResponse(
        request,
        "dashboard.html",
        {"csrf_token": csrf},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    from app.auth import require_csrf

    require_csrf(request)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=_secure_cookie(request), httponly=True, samesite="strict")
    return response
