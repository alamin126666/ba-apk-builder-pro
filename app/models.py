from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BuildJob:
    id: str
    app_name: str
    package_name: str
    project_upload: Path
    project_filename: str
    logo_upload: Path | None
    status: str = "receiving"
    stage: str = "Receiving files"
    progress: int = 0
    error: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
