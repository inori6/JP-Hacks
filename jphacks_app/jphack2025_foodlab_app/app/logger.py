"""Helpers for writing app activity and HTTP logs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def _log_dir(user_data_dir: str | Path) -> Path:
    path = Path(user_data_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _log_path(user_data_dir: str | Path) -> Path:
    return _log_dir(user_data_dir) / "service.log"


def log_event(user_data_dir: str | Path, message: str) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    _append_line(user_data_dir, f"{ts} {message}")


def log_http(
    user_data_dir: str | Path,
    method: str,
    url: str,
    status: str,
    elapsed_ms: float,
    note: str = "",
) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    suffix = f" {note}" if note else ""
    line = f"{ts} {method.upper()} {url} {status} {elapsed_ms:.0f}ms{suffix}"
    _append_line(user_data_dir, line)


def _append_line(user_data_dir: str | Path, line: str) -> None:
    path = _log_path(user_data_dir)
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        # Avoid raising during logging in production flows.
        pass
