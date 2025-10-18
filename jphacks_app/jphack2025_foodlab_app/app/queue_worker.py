"""Background worker that processes the offline upload queue."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import httpx
from kivy.clock import Clock

from . import api_client, db
from .i18n import _
from .logger import log_event


StatusCallback = Callable[[str, str, str], None]


class QueueWorker:
    def __init__(
        self,
        db_path: Path,
        user_data_dir: str | Path,
        settings_provider: Callable[[], db.AppSettings],
        status_callback: Optional[StatusCallback] = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._user_data_dir = str(user_data_dir)
        self._settings_provider = settings_provider
        self._status_callback = status_callback
        settings = self._settings_provider()
        self._client = api_client.ApiClient(settings.base_url, settings.api_key, user_data_dir)
        self._thread: Optional[threading.Thread] = None
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="QueueWorker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def wake(self) -> None:
        self._wake_event.set()

    def reconfigure(self) -> None:
        settings = self._settings_provider()
        self._client.configure(settings.base_url, settings.api_key)
        self.wake()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            entry = db.next_pending_entry(self._db_path)
            if not entry:
                self._wait_for_signal()
                continue
            self._wake_event.clear()
            self._process(entry)

    def _wait_for_signal(self) -> None:
        self._wake_event.wait(timeout=5.0)
        self._wake_event.clear()

    def _process(self, entry: db.FoodEntry) -> None:
        message = ""
        try:
            self._notify(entry.id, "processing", _("status_uploading"))
            db.update_sync_state(self._db_path, entry.id, "processing")
            upload_id = self._upload(entry)
            self._notify(entry.id, "processing", _("status_analyzing"))
            analysis = self._client.analyze_upload(upload_id)
            self._notify(entry.id, "processing", _("status_registering"))
            payload = _build_item_payload(entry, upload_id, analysis)
            item_resp = self._client.create_item(payload)
            item_id = str(item_resp.get("id") or item_resp.get("item_id") or "")
            fields = _extract_fields(analysis)
            db.record_attempt_result(
                self._db_path,
                entry.id,
                success=True,
                upload_id=upload_id,
                item_id=item_id or None,
                class_id=fields.class_id,
                name=fields.name,
                deadline_ts=fields.deadline_ts,
                storage=fields.storage,
                storage_label=fields.storage_label,
                note=fields.note,
                ripeness=fields.ripeness,
                hours_left=fields.hours_left,
                analysis_payload=analysis,
            )
            message = _("status_completed", item_id or upload_id)
            self._notify(entry.id, "done", message)
        except httpx.HTTPStatusError as exc:
            error_message = _map_status_error(exc)
            log_event(self._user_data_dir, f"queue error {entry.id}: {error_message}")
            db.record_attempt_result(
                self._db_path,
                entry.id,
                success=False,
                error=error_message,
            )
            self._notify(entry.id, "error", error_message)
            time.sleep(2.0)
        except (httpx.RequestError, api_client.ApiError) as exc:
            error_message = _("error_network")
            if isinstance(exc, api_client.ApiError):
                error_message = str(exc)
            log_event(self._user_data_dir, f"queue failure {entry.id}: {error_message}")
            db.record_attempt_result(
                self._db_path,
                entry.id,
                success=False,
                error=error_message,
            )
            self._notify(entry.id, "error", error_message)
            time.sleep(3.0)
        except Exception as exc:  # pragma: no cover - defensive
            error_message = _("error_generic", exc)
            log_event(self._user_data_dir, f"queue exception {entry.id}: {exc}")
            db.record_attempt_result(
                self._db_path,
                entry.id,
                success=False,
                error=error_message,
            )
            self._notify(entry.id, "error", error_message)
            time.sleep(3.0)

    def _upload(self, entry: db.FoodEntry) -> str:
        if not entry.image_path:
            raise api_client.ApiError("画像パスがありません")
        path = Path(entry.image_path)
        if not path.exists():
            raise api_client.ApiError("画像が見つかりません")
        upload_resp = self._client.upload_image(path, storage=entry.storage or "cool")
        upload_id = str(upload_resp.get("upload_id") or upload_resp.get("id") or "").strip()
        if not upload_id:
            raise api_client.ApiError("upload_id が取得できませんでした")
        return upload_id

    def _notify(self, entry_id: str, state: str, message: str) -> None:
        if not self._status_callback:
            return

        def _dispatch(_dt):
            self._status_callback(entry_id, state, message)

        Clock.schedule_once(_dispatch, 0)


class ExtractedFields:
    def __init__(
        self,
        name: Optional[str] = None,
        class_id: Optional[str] = None,
        deadline_ts: Optional[int] = None,
        storage: Optional[str] = None,
        storage_label: Optional[str] = None,
        note: Optional[str] = None,
        ripeness: Optional[str] = None,
        hours_left: Optional[float] = None,
    ) -> None:
        self.name = name
        self.class_id = class_id
        self.deadline_ts = deadline_ts
        self.storage = storage
        self.storage_label = storage_label
        self.note = note
        self.ripeness = ripeness
        self.hours_left = hours_left


def _extract_fields(data: dict) -> ExtractedFields:
    name = _first_present(data, ["name", "label", "title"])
    class_id = _first_present(data, ["class_id", "class", "class_raw"])
    storage = _first_present(data, ["storage", "storage_code"])
    storage_label = _first_present(data, ["storage_label", "storage_hint", "storage_name"])
    note = data.get("note")
    ripeness = _first_present(data, ["ripeness", "maturity"])
    hours_left = _safe_float(data.get("hours_left"))
    deadline_ts = _parse_deadline(data)
    return ExtractedFields(
        name=name,
        class_id=class_id,
        deadline_ts=deadline_ts,
        storage=storage,
        storage_label=storage_label,
        note=note,
        ripeness=ripeness,
        hours_left=hours_left,
    )


def _build_item_payload(entry: db.FoodEntry, upload_id: str, analysis: dict) -> dict:
    fields = _extract_fields(analysis)
    payload = {
        "upload_id": upload_id,
        "name": fields.name or entry.name,
        "class_id": fields.class_id,
        "storage": fields.storage or entry.storage or "cool",
        "note": fields.note,
        "ripeness": fields.ripeness,
        "hours_left": fields.hours_left,
        "analysis": analysis,
    }
    if fields.deadline_ts:
        payload["expiry"] = _format_iso(fields.deadline_ts)
    clean_payload = {k: v for k, v in payload.items() if v not in (None, "")}
    return clean_payload


def _format_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _parse_deadline(data: dict) -> Optional[int]:
    candidates = [
        data.get("deadline_ts"),
        data.get("deadline"),
        data.get("expiry_ts"),
        data.get("expiry"),
    ]
    for value in candidates:
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.strip():
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
    return None


def _first_present(data: dict, keys: list[str]) -> Optional[str]:
    for key in keys:
        value = data.get(key)
        if value:
            return str(value)
    return None


def _safe_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _map_status_error(exc: httpx.HTTPStatusError) -> str:
    code = exc.response.status_code
    if code == 401:
        return _("error_unauthorized")
    if code == 400:
        return _("error_bad_request")
    if 500 <= code < 600:
        return _("error_server")
    return f"HTTP {code}: {exc.response.text[:120]}"
