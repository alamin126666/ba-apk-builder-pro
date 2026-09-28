from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status

from app.security import SessionCookies


def session_cookies(request: Request) -> SessionCookies:
    return request.app.state.sessions


def require_csrf(request: Request) -> str:
    csrf = session_cookies(request).verify(request.cookies.get("apk_builder_session"))
    supplied = request.headers.get("x-csrf-token")
    if not SessionCookies.check_csrf(csrf, supplied):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The page session expired. Refresh and try again.")
    return csrf


def require_auth(request: Request) -> str:
    csrf = session_cookies(request).verify(request.cookies.get("apk_builder_session"))
    if csrf is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue.")
    return csrf


def password_matches(candidate: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))
