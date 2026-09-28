from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_PASSWORD = "test-only-admin-password-49f0eabbbd"


@pytest.fixture
def app_settings(tmp_path: Path) -> Settings:
    return Settings(
        admin_password=TEST_PASSWORD,
        session_secret="test-only-session-secret-923b1d07e7",
        max_upload_mb=2,
        max_concurrent_builds=1,
        build_retention_hours=24,
        build_root=tmp_path / "build-root",
        project_root=PROJECT_ROOT,
    )


@pytest.fixture
def client(app_settings: Settings):
    app = create_app(app_settings)
    with TestClient(app) as test_client:
        yield test_client
