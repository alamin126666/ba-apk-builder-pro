from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from html import escape

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_PACKAGE_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_BUILD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def validate_app_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip()
    if not value or len(value) > 50:
        raise ValueError("App name must contain 1 to 50 characters.")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("App name contains unsupported control characters.")
    return value


def validate_package_name(value: str) -> str:
    value = value.strip()
    if len(value) > 255 or not _PACKAGE_RE.fullmatch(value):
        raise ValueError("Use a valid package name such as com.example.myapp (lowercase letters, digits, and underscores).")
    if value.startswith(("java.", "javax.", "android.")):
        raise ValueError("Package names cannot use reserved Android or Java namespaces.")
    return value


def xml_attribute(value: str) -> str:
    return escape(value, quote=True)


def valid_build_id(value: str) -> bool:
    return bool(_BUILD_ID_RE.fullmatch(value))


class SessionCookies:
    def __init__(self, secret: str):
        self.serializer = URLSafeTimedSerializer(secret, salt="apk-builder-auth-v1")
        self.csrf_secret = hashlib.sha256((secret + ":csrf").encode()).digest()

    def create(self) -> tuple[str, str]:
        token = self.serializer.dumps({"authenticated": True})
        csrf = hmac.new(self.csrf_secret, token.encode(), hashlib.sha256).hexdigest()
        return token, csrf

    def verify(self, token: str | None, max_age: int = 60 * 60 * 12) -> str | None:
        if not token:
            return None
        try:
            payload = self.serializer.loads(token, max_age=max_age)
        except (BadSignature, SignatureExpired):
            return None
        if payload != {"authenticated": True}:
            return None
        return hmac.new(self.csrf_secret, token.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def check_csrf(expected: str | None, supplied: str | None) -> bool:
        return bool(expected and supplied and hmac.compare_digest(expected, supplied))
