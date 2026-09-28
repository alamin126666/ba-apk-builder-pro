from __future__ import annotations

import threading

import pytest

from app.builder import BuildCancelled, _check_cancel


def test_check_cancel_allows_active_build() -> None:
    _check_cancel(threading.Event())


def test_check_cancel_stops_cancelled_build() -> None:
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(BuildCancelled):
        _check_cancel(cancel_event)
