from __future__ import annotations

import io
import os
import threading

import pytest

import app.builder as builder
from app.builder import BuildCancelled, BuildFailure, BoundedBuildLog, _check_cancel, _gradle_failure_detail, _run_gradle


def test_check_cancel_allows_active_build() -> None:
    _check_cancel(threading.Event())


def test_check_cancel_stops_cancelled_build() -> None:
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(BuildCancelled):
        _check_cancel(cancel_event)


def test_gradle_failure_detail_redacts_paths_and_sensitive_lines(tmp_path) -> None:
    log_path = tmp_path / "build.log"
    log_path.write_text(
        "FAILURE: Build failed with an exception.\n"
        "* What went wrong:\n"
        "Execution failed for task ':app:compileDebugKotlin'.\n"
        "Gradle build daemon disappeared unexpectedly (it may have been killed or may have crashed).\n"
        "> e: /opt/apk-builder/job123/build/src/MainActivity.kt:18:1: unresolved reference\n"
        "Execution key password: do-not-display\n",
        encoding="utf-8",
    )

    detail = _gradle_failure_detail(log_path)

    assert detail is not None
    assert "compileDebugKotlin" in detail
    assert "daemon disappeared unexpectedly" in detail
    assert "<path>" in detail
    assert "/opt/apk-builder" not in detail
    assert "do-not-display" not in detail


def test_gradle_timeout_stops_child_and_reports_safe_error(tmp_path, monkeypatch) -> None:
    class StalledProcess:
        def __init__(self):
            self.stdout = io.StringIO("")
            self.stopped = False

        def poll(self):
            return 0 if self.stopped else None

        def terminate(self):
            self.stopped = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.stopped = True

    process = StalledProcess()
    monkeypatch.setattr(builder.subprocess, "Popen", lambda *args, **kwargs: process)
    project = tmp_path / "android"
    project.mkdir()
    (project / ("gradlew.bat" if os.name == "nt" else "gradlew")).write_text("", encoding="utf-8")
    log = BoundedBuildLog(tmp_path / "build.log")
    try:
        with pytest.raises(BuildFailure, match="time limit"):
            _run_gradle(project, {}, log, lambda *_: None, threading.Event(), timeout_seconds=0.01)
        assert process.stopped
    finally:
        log.close()
