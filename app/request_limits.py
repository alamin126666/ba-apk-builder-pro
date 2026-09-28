from __future__ import annotations

from starlette.responses import JSONResponse


class RequestBodyTooLarge(Exception):
    pass


class BuildUploadLimitMiddleware:
    """Bound multipart parsing before FastAPI's UploadFile spooling begins."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_body_bytes = max_bytes + 1024 * 1024

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") != "/api/build":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        try:
            content_length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            content_length = 0
        if content_length > self.max_body_bytes:
            response = JSONResponse({"detail": "The upload exceeds the configured size limit."}, status_code=413)
            await response(scope, receive, send)
            return
        consumed = 0

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            if message.get("type") == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_body_bytes:
                    raise RequestBodyTooLarge()
            return message

        await self.app(scope, limited_receive, send)
