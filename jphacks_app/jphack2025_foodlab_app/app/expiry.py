"""Expiry helpers for local/offline evaluation.

The original implementation (`syncer.py`) delegated expiry decisions to the
backend.  Here we provide a deterministic local strategy so the MVP remains
functional without the remote service.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
import math
from typing import Iterable, List, Tuple

from .db import FoodEntry
from .i18n import _

# TODO(syncer.py): align expiry logic with backend once service is restored.
DEFAULT_SHELF_LIFE_HOURS = 48
SOON_THRESHOLD_HOURS = 24


@dataclass
class Notification:
    entry_id: str
    title: str
    message: str


@dataclass
class EvaluationResult:
    updates: List[Tuple[str, str]]
    notifications: List[Notification]


def default_deadline(hours: int = DEFAULT_SHELF_LIFE_HOURS) -> int:
    return int(time.time() + hours * 3600)


def classify(entry: FoodEntry, now: float | None = None) -> tuple[str, float]:
    now = now or time.time()
    if not entry.deadline_ts:
        return entry.status or "PENDING", float("inf")
    remaining = entry.deadline_ts - now
    hours_left = remaining / 3600
    if remaining <= 0:
        return "EXPIRED", hours_left
    if hours_left <= SOON_THRESHOLD_HOURS:
        return "DUE_SOON", hours_left
    return "FRESH", hours_left


def describe(entry: FoodEntry, now: float | None = None) -> str:
    status, hours_left = classify(entry, now)
    status_label = {
        "FRESH": _("fresh"),
        "DUE_SOON": _("due_soon"),
        "EXPIRED": _("expired"),
    }.get(status, status)
    name = entry.name or _("unknown_name")

    created_line = _("created_label", _format_timestamp(entry.created_at))
    if entry.deadline_ts:
        expiry_line = _("expiry_label", _format_timestamp(entry.deadline_ts))
    else:
        expiry_line = _("expiry_unknown")

    if hours_left == float("inf"):
        remaining_line = _("remaining_unknown")
    elif status == "EXPIRED":
        remaining_line = _("remaining_expired")
    elif hours_left >= 24:
        days = max(math.ceil(hours_left / 24), 0)
        remaining_line = _("days_left", days)
    else:
        hours = max(math.ceil(hours_left), 0)
        remaining_line = _("hours_left", hours)

    return "\n".join([f"{status_label} {name}", created_line, expiry_line, remaining_line])


def evaluate(entries: Iterable[FoodEntry], now: float | None = None) -> EvaluationResult:
    now = now or time.time()
    updates: List[Tuple[str, str]] = []
    notifications: List[Notification] = []
    for entry in entries:
        new_status, hours_left = classify(entry, now)
        if new_status != entry.status:
            updates.append((entry.id, new_status))
        if new_status in {"DUE_SOON", "EXPIRED"} and new_status != entry.status:
            name = entry.name or _("unknown_name")
            if new_status == "DUE_SOON":
                title = _("notify_title_due")
                message = _("notify_body_due", name)
            else:
                title = _("notify_title_expired")
                message = _("notify_body_expired", name)
            notifications.append(Notification(entry_id=entry.id, title=title, message=message))
    return EvaluationResult(updates=updates, notifications=notifications)


def _format_timestamp(ts: int) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
