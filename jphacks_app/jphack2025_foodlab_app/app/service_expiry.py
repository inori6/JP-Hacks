"""Background expiry checker service."""

from __future__ import annotations

import os
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import List

from plyer import notification

from . import db
from .i18n import _

INTERVAL = int(os.environ.get("EXPIRY_CHECK_INTERVAL_SEC", 6 * 60 * 60))
THRESH = int(os.environ.get("EXPIRY_THRESHOLD_DAYS", 3))


def _user_data_dir() -> Path:
    if os.environ.get("FOODLAB_USER_DATA_DIR"):
        return Path(os.environ["FOODLAB_USER_DATA_DIR"])
    try:
        from jnius import autoclass

        service = autoclass('org.kivy.android.PythonService').mService
        if service:
            return Path(service.getFilesDir().getAbsolutePath())
    except Exception:
        pass
    return Path(os.getcwd())


def _log_path() -> Path:
    user_dir = _user_data_dir()
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / "service.log"


def _write_log(message: str) -> None:
    try:
        with open(_log_path(), "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except Exception:
        pass


def _format_items(items: List[dict]) -> str:
    names = [item.get("name") or _("unknown_name") for item in items]
    return " / ".join(names[:3])


def main() -> None:
    user_dir = _user_data_dir()
    db_path = db.ensure_database(user_dir, seed_demo=False)
    _write_log(f"{datetime.now().isoformat()} service start")

    while True:
        try:
            items = db.due_within(db_path, THRESH)
            if items:
                body = _format_items(items)
                notification.notify(
                    title=_("notify_title_due"),
                    message=body,
                )
                _write_log(
                    f"{datetime.now().isoformat()} notify {len(items)} items: {body}"
                )
            else:
                _write_log(f"{datetime.now().isoformat()} no due items")
        except Exception:
            _write_log(traceback.format_exc())
        time.sleep(max(INTERVAL, 60))


if __name__ == "__main__":
    main()
