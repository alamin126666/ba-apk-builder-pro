from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path

from app.build_manager import BuildManager
from app.config import Settings
from conftest import TEST_PASSWORD


def test_health_and_private_dashboard(client) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert client.get("/dashboard", follow_redirects=False).status_code == 303
    assert client.get("/api/builds").status_code == 401


def test_login_cookie_flags_and_csrf(client) -> None:
    rejected = client.post("/login", data={"password": "wrong"}, follow_redirects=False)
    assert rejected.status_code == 401
    accepted = client.post("/login", data={"password": TEST_PASSWORD}, follow_redirects=False)
    assert accepted.status_code == 303
    cookie = accepted.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/dashboard").status_code == 200
    assert client.post("/logout").status_code == 403
    csrf = re.search(r'<meta name="csrf-token" content="([0-9a-f]+)"', client.get("/dashboard").text).group(1)
    assert client.post("/logout", headers={"X-CSRF-Token": csrf}, follow_redirects=False).status_code == 303
    assert client.get("/api/builds").status_code == 401


def test_authenticated_status_and_download_do_not_accept_unknown_ids(client) -> None:
    client.post("/login", data={"password": TEST_PASSWORD}, follow_redirects=False)
    csrf = re.search(r'<meta name="csrf-token" content="([0-9a-f]+)"', client.get("/dashboard").text).group(1)
    unknown = uuid.uuid4().hex
    assert client.get(f"/api/build/{unknown}/status").status_code == 404
    assert client.get(f"/api/build/{unknown}/download").status_code == 404
    assert client.post(f"/api/build/{unknown}/cancel", headers={"X-CSRF-Token": csrf}).status_code == 404
    assert client.get("/api/build/../../etc/passwd/status").status_code in {404, 405}


def test_upload_request_body_limit(client) -> None:
    response = client.post("/api/build", content=b"x" * (3 * 1024 * 1024 + 1))
    assert response.status_code == 413
    assert response.json()["detail"] == "The upload exceeds the configured size limit."


def test_build_workspace_isolation(app_settings: Settings) -> None:
    async def reserve_two() -> tuple[Path, Path]:
        manager = BuildManager(app_settings)
        manager.start()
        first = manager.reserve("First", "com.example.first", "site.zip")
        second = manager.reserve("Second", "com.example.second", "site.zip")
        try:
            assert first.id != second.id
            assert first.project_upload.parent != second.project_upload.parent
            assert first.project_upload.parent.is_dir() and second.project_upload.parent.is_dir()
            return first.project_upload.parent, second.project_upload.parent
        finally:
            manager.discard(first)
            manager.discard(second)
            await manager.close()

    first_dir, second_dir = asyncio.run(reserve_two())
    assert first_dir != second_dir
    assert not first_dir.exists() and not second_dir.exists()
