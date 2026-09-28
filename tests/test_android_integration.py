from __future__ import annotations

import os
import time
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from conftest import PROJECT_ROOT, TEST_PASSWORD


@pytest.mark.android_integration
@pytest.mark.skipif(os.getenv("RUN_ANDROID_INTEGRATION") != "1", reason="Requires an installed Android SDK/NDK.")
def test_real_debug_apk_contains_only_protected_web_assets(tmp_path: Path) -> None:
    settings = Settings(
        admin_password=TEST_PASSWORD,
        session_secret="integration-session-secret-6c2d9a7",
        max_upload_mb=25,
        max_concurrent_builds=1,
        build_retention_hours=24,
        build_root=tmp_path / "build-root",
        project_root=PROJECT_ROOT,
    )
    app = create_app(settings)
    icon = tmp_path / "icon.png"
    Image.new("RGBA", (256, 256), "#6555e8").save(icon)
    with TestClient(app) as client:
        client.post("/login", data={"password": TEST_PASSWORD}, follow_redirects=False)
        import re

        csrf = re.search(r'<meta name="csrf-token" content="([0-9a-f]+)"', client.get("/dashboard").text).group(1)
        response = client.post(
            "/api/build",
            data={"app_name": "Test Web App", "package_name": "com.example.testwebapp"},
            files={
                "web_project": ("index.html", b"<!doctype html><html><head><title>Integration payload</title><link rel='stylesheet' href='css/app.css'></head><body><h1>Protected page</h1><script src='js/app.js'></script></body></html>", "text/html"),
                "logo": ("icon.png", icon.read_bytes(), "image/png"),
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 202, response.text
        build_id = response.json()["build"]["id"]
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            build = client.get(f"/api/build/{build_id}/status").json()["build"]
            if build["status"] in {"success", "failed", "cancelled"}:
                break
            time.sleep(2)
        assert build["status"] == "success", build
        apk_response = client.get(build["download_url"])
        assert apk_response.status_code == 200
        apk = tmp_path / "debug.apk"
        apk.write_bytes(apk_response.content)
        with zipfile.ZipFile(apk) as archive:
            names = set(archive.namelist())
            assert "assets/app.dat" in names
            assert not any(name == "assets/index.html" or name.startswith(("assets/css/", "assets/js/", "assets/images/")) for name in names)
            assert any(name.startswith("lib/") and name.endswith("/libprotected_loader.so") for name in names)
            apk_bytes = apk.read_bytes()
            assert b"Protected page" not in apk_bytes
            assert b"Integration payload" not in apk_bytes
