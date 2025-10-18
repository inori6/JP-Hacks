"""Notification helpers wrapping `plyer.notification`.

In the legacy stack, notifications were triggered from ``syncer.py``.  The
test harness keeps the behaviour lightweight so the rest of the app can call a
single function without worrying about platform quirks.
"""

from __future__ import annotations

from plyer import notification


def send(title: str, message: str) -> None:
    try:
        notification.notify(title=title, message=message, timeout=5)
    except Exception as exc:  # pragma: no cover - platform specific
        # Keep the failure silent but visible in logs for debugging.
        print(f"[notify] failed to send notification: {exc}")
