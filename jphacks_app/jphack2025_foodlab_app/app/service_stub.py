"""Minimal background scheduler used in lieu of the original sync service."""

# TODO(syncer.py): replace with a robust background worker when porting the
# network upload pipeline.

from __future__ import annotations

from typing import Callable, Optional

from kivy.clock import Clock


def start_periodic_checks(callback: Callable[[], None], interval_seconds: float = 3600.0):
    """Schedule ``callback`` to run every ``interval_seconds``.

    Returns the scheduled event so callers can cancel it during app shutdown.
    """

    return Clock.schedule_interval(lambda _dt: callback(), interval_seconds)


def stop_periodic_checks(event) -> None:
    if event is not None:
        event.cancel()
