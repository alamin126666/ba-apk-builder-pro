from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    admin_password: str
    session_secret: str
    max_upload_mb: int
    max_concurrent_builds: int
    build_retention_hours: int
    build_root: Path
    project_root: Path
    queue_size: int = 20
    build_timeout_seconds: int = 1200

    @classmethod
    def from_env(cls) -> "Settings":
        project_root = Path(__file__).resolve().parent.parent
        admin_password = os.getenv("ADMIN_PASSWORD", "")
        secret = os.getenv("SESSION_SECRET", "")
        if not secret:
            # Password rotation invalidates existing cookies without requiring another secret.
            import hashlib

            secret = hashlib.sha256(("private-apk-builder-session:" + admin_password).encode()).hexdigest()
        return cls(
            admin_password=admin_password,
            session_secret=secret,
            max_upload_mb=_positive_int("MAX_UPLOAD_MB", 25, maximum=512),
            max_concurrent_builds=_positive_int("MAX_CONCURRENT_BUILDS", 1, maximum=8),
            build_retention_hours=_positive_int("BUILD_RETENTION_HOURS", 24, maximum=720),
            build_root=Path(os.getenv("BUILD_ROOT", str(Path(tempfile.gettempdir()) / "apk-builder"))).resolve(),
            project_root=project_root,
            build_timeout_seconds=_positive_int("BUILD_TIMEOUT_SECONDS", 1200, maximum=7200),
        )


def _positive_int(name: str, default: int, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except ValueError as exc:
        raise ValueError(f"{name} must be a whole number") from exc
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value
