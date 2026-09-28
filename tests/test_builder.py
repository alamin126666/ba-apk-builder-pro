from __future__ import annotations

import threading

import pytest

from app.builder import BuildCancelled, _check_cancel, _gradle_failure_detail


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
        "> e: /opt/apk-builder/job123/build/src/MainActivity.kt:18:1: unresolved reference\n"
        "Execution key password: do-not-display\n",
        encoding="utf-8",
    )

    detail = _gradle_failure_detail(log_path)

    assert detail is not None
    assert "compileDebugKotlin" in detail
    assert "<path>" in detail
    assert "/opt/apk-builder" not in detail
    assert "do-not-display" not in detail
