"""SQLite helpers for the production workflow with migrations and settings."""

from __future__ import annotations

import base64
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

DEFAULT_BASE_URL = "https://lab.160.16.126.35.sslip.io"


# ---------------------------------------------------------------------------
# Dataclasses


@dataclass
class AppSettings:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""


@dataclass
class FoodEntry:
    id: str
    name: str
    image_path: Optional[str]
    image_sha1: Optional[str]
    status: str
    deadline_ts: Optional[int]
    created_at: int
    updated_at: int
    sync_state: str
    attempt_count: int
    last_error: Optional[str]
    upload_id: Optional[str]
    item_id: Optional[str]
    class_id: Optional[str]
    storage: Optional[str]
    storage_label: Optional[str]
    note: Optional[str]
    ripeness: Optional[str]
    hours_left: Optional[float]
    last_synced_at: Optional[int]
    analysis_payload: Optional[str]

    def analysis_data(self) -> Optional[dict]:
        if not self.analysis_payload:
            return None
        try:
            return json.loads(self.analysis_payload)
        except json.JSONDecodeError:
            return None


# ---------------------------------------------------------------------------
# Schema & migrations


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS food (
    id TEXT PRIMARY KEY,
    name TEXT,
    image_path TEXT,
    image_sha1 TEXT,
    status TEXT,
    deadline_ts INTEGER,
    created_at INTEGER,
    updated_at INTEGER,
    sync_state TEXT,
    attempt_count INTEGER,
    last_error TEXT,
    upload_id TEXT,
    item_id TEXT,
    class_id TEXT,
    storage TEXT,
    storage_label TEXT,
    note TEXT,
    ripeness TEXT,
    hours_left REAL,
    last_synced_at INTEGER,
    analysis_payload TEXT
);
"""


CREATE_SETTINGS_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


ADD_COLUMNS_SQL = {
    "name": "ALTER TABLE food ADD COLUMN name TEXT",
    "image_sha1": "ALTER TABLE food ADD COLUMN image_sha1 TEXT",
    "status": "ALTER TABLE food ADD COLUMN status TEXT",
    "deadline_ts": "ALTER TABLE food ADD COLUMN deadline_ts INTEGER",
    "created_at": "ALTER TABLE food ADD COLUMN created_at INTEGER",
    "updated_at": "ALTER TABLE food ADD COLUMN updated_at INTEGER",
    "sync_state": "ALTER TABLE food ADD COLUMN sync_state TEXT DEFAULT 'pending'",
    "attempt_count": "ALTER TABLE food ADD COLUMN attempt_count INTEGER DEFAULT 0",
    "last_error": "ALTER TABLE food ADD COLUMN last_error TEXT",
    "upload_id": "ALTER TABLE food ADD COLUMN upload_id TEXT",
    "item_id": "ALTER TABLE food ADD COLUMN item_id TEXT",
    "class_id": "ALTER TABLE food ADD COLUMN class_id TEXT",
    "storage": "ALTER TABLE food ADD COLUMN storage TEXT",
    "storage_label": "ALTER TABLE food ADD COLUMN storage_label TEXT",
    "note": "ALTER TABLE food ADD COLUMN note TEXT",
    "ripeness": "ALTER TABLE food ADD COLUMN ripeness TEXT",
    "hours_left": "ALTER TABLE food ADD COLUMN hours_left REAL",
    "last_synced_at": "ALTER TABLE food ADD COLUMN last_synced_at INTEGER",
    "analysis_payload": "ALTER TABLE food ADD COLUMN analysis_payload TEXT"
}


CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_food_sync_state ON food(sync_state)",
    "CREATE INDEX IF NOT EXISTS idx_food_created_at ON food(created_at DESC)",
]


def ensure_database(user_data_dir: str | Path, *, seed_demo: bool = False) -> Path:
    base = Path(user_data_dir)
    base.mkdir(parents=True, exist_ok=True)
    db_path = base / "food.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(CREATE_TABLE_SQL)
        conn.execute(CREATE_SETTINGS_SQL)
        _apply_migrations(conn)
        for stmt in CREATE_INDEXES:
            conn.execute(stmt)
        conn.commit()
    if seed_demo:
        seed_demo_if_empty(db_path)
    return db_path


def _apply_migrations(conn: sqlite3.Connection) -> None:
    columns = _existing_columns(conn, "food")
    for name, ddl in ADD_COLUMNS_SQL.items():
        if name not in columns:
            conn.execute(ddl)


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def seed_demo_if_empty(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute("SELECT COUNT(*) FROM food")
        (count,) = cur.fetchone()
        if count:
            return
        now = int(time.time())
        rows = [
            {
                "id": f"demo-{i}",
                "name": label,
                "image_path": None,
                "image_sha1": None,
                "status": "PENDING",
                "deadline_ts": now + delta,
                "created_at": now,
                "updated_at": now,
                "sync_state": "done",
                "attempt_count": 0,
                "last_error": None,
                "upload_id": None,
                "item_id": None,
                "class_id": None,
                "storage": "cool",
                "storage_label": "要冷蔵",
                "note": None,
                "ripeness": None,
                "hours_left": 48.0,
                "last_synced_at": now,
                "analysis_payload": None,
            }
            for i, (label, delta) in enumerate(
                [("サンプルりんご", 48 * 3600), ("サンプルぶどう", 72 * 3600)]
            )
        ]
        for row in rows:
            placeholders = ",".join([":" + key for key in row.keys()])
            columns = ",".join(row.keys())
            conn.execute(
                f"INSERT INTO food({columns}) VALUES({placeholders})",
                row,
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Settings helpers


_SETTINGS_KEYS = {
    "base_url": "app.base_url",
    "api_key": "app.api_key",
}


def load_settings(db_path: Path) -> AppSettings:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    data = {row["key"]: row["value"] for row in rows}
    raw_base_url = data.get(_SETTINGS_KEYS["base_url"], DEFAULT_BASE_URL)
    base_url = _normalize_base_url(raw_base_url)
    api_key = _decode_value(data.get(_SETTINGS_KEYS["api_key"], ""))
    api_key = api_key.strip()
    return AppSettings(base_url=base_url, api_key=api_key)


def save_settings(db_path: Path, settings: AppSettings) -> None:
    normalized_base = _normalize_base_url(settings.base_url)
    api_key = settings.api_key.strip()
    payload = {
        _SETTINGS_KEYS["base_url"]: normalized_base,
        _SETTINGS_KEYS["api_key"]: _encode_value(api_key),
    }
    with sqlite3.connect(db_path) as conn:
        for key, value in payload.items():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        conn.commit()


def _normalize_base_url(value: str | None) -> str:
    candidate = (value or "").strip().rstrip('/')
    if not candidate:
        return DEFAULT_BASE_URL
    return candidate


def _encode_value(value: str) -> str:
    if not value:
        return ""
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")


def _decode_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")
    except Exception:
        return value


# ---------------------------------------------------------------------------
# CRUD helpers for food entries


def insert_photo_entry(
    db_path: Path,
    name: str,
    image_path: Path,
    image_sha1: str,
    deadline_ts: Optional[int],
    storage: str = "cool",
) -> str:
    entry_id = f"local-{int(time.time() * 1000)}"
    now = int(time.time())
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO food(" \
            "id, name, image_path, image_sha1, status, deadline_ts, created_at, updated_at, " \
            "sync_state, attempt_count, storage)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                entry_id,
                name,
                str(image_path),
                image_sha1,
                "PENDING",
                deadline_ts,
                now,
                now,
                "pending",
                0,
                storage,
            ),
        )
        conn.commit()
    return entry_id


def fetch_entries(db_path: Path) -> List[FoodEntry]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, name, image_path, image_sha1, status, deadline_ts, created_at, updated_at, "
            "sync_state, attempt_count, last_error, upload_id, item_id, class_id, storage, storage_label, "
            "note, ripeness, hours_left, last_synced_at, analysis_payload "
            "FROM food ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_entry(row) for row in rows]


def get_entry(db_path: Path, entry_id: str) -> Optional[FoodEntry]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, name, image_path, image_sha1, status, deadline_ts, created_at, updated_at, "
            "sync_state, attempt_count, last_error, upload_id, item_id, class_id, storage, storage_label, "
            "note, ripeness, hours_left, last_synced_at, analysis_payload "
            "FROM food WHERE id=?",
            (entry_id,),
        ).fetchone()
    return _row_to_entry(row) if row else None


def next_pending_entry(db_path: Path, *, retry_delay_seconds: int = 60) -> Optional[FoodEntry]:
    now = int(time.time())
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, name, image_path, image_sha1, status, deadline_ts, created_at, updated_at, "
            "sync_state, attempt_count, last_error, upload_id, item_id, class_id, storage, storage_label, "
            "note, ripeness, hours_left, last_synced_at, analysis_payload "
            "FROM food WHERE sync_state = 'pending' "
            "   OR (sync_state = 'error' AND (COALESCE(updated_at, 0) + ?) <= ?) "
            "ORDER BY updated_at ASC LIMIT 1",
            (retry_delay_seconds, now),
        ).fetchone()
    return _row_to_entry(row) if row else None


def update_sync_state(db_path: Path, entry_id: str, state: str, *, error: Optional[str] = None) -> None:
    now = int(time.time())
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE food SET sync_state = ?, last_error = ?, updated_at = ?, attempt_count = attempt_count + ? "
            "WHERE id = ?",
            (state, error, now, 1 if state == "processing" else 0, entry_id),
        )
        conn.commit()


def record_attempt_result(
    db_path: Path,
    entry_id: str,
    *,
    success: bool,
    upload_id: Optional[str] = None,
    item_id: Optional[str] = None,
    class_id: Optional[str] = None,
    name: Optional[str] = None,
    deadline_ts: Optional[int] = None,
    storage: Optional[str] = None,
    storage_label: Optional[str] = None,
    note: Optional[str] = None,
    ripeness: Optional[str] = None,
    hours_left: Optional[float] = None,
    analysis_payload: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    now = int(time.time())
    payload = {
        "sync_state": "done" if success else "error",
        "last_error": error,
        "upload_id": upload_id,
        "item_id": item_id,
        "class_id": class_id,
        "name": name,
        "deadline_ts": deadline_ts,
        "storage": storage,
        "storage_label": storage_label,
        "note": note,
        "ripeness": ripeness,
        "hours_left": hours_left,
        "last_synced_at": now if success else None,
        "analysis_payload": json.dumps(analysis_payload) if analysis_payload else None,
        "updated_at": now,
        "status": _status_from_hours(hours_left, deadline_ts),
    }
    assignments = []
    values: list[object] = []
    for key, value in payload.items():
        if value is not None:
            assignments.append(f"{key} = ?")
            values.append(value)
    if not assignments:
        return
    values.append(entry_id)
    set_clause = ", ".join(assignments)
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"UPDATE food SET {set_clause} WHERE id = ?", values)
        conn.commit()


def _status_from_hours(hours_left: Optional[float], deadline_ts: Optional[int]) -> str:
    if hours_left is None and deadline_ts is None:
        return "PENDING"
    if hours_left is not None:
        if hours_left <= 0:
            return "EXPIRED"
        if hours_left <= 24:
            return "DUE_SOON"
        return "FRESH"
    if deadline_ts:
        remaining = deadline_ts - int(time.time())
        if remaining <= 0:
            return "EXPIRED"
        if remaining <= 24 * 3600:
            return "DUE_SOON"
        return "FRESH"
    return "PENDING"


def apply_status_updates(db_path: Path, updates: Iterable[Tuple[str, str]]) -> None:
    updates = list(updates)
    if not updates:
        return
    now = int(time.time())
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "UPDATE food SET status = ?, updated_at = ? WHERE id = ?",
            [(status, now, entry_id) for entry_id, status in updates],
        )
        conn.commit()


def load_entry_map(db_path: Path) -> dict[str, FoodEntry]:
    return {entry.id: entry for entry in fetch_entries(db_path)}


def due_within(db_path: Path, threshold_days: int, now: Optional[int] = None) -> List[dict]:
    now = int(now or time.time())
    upper_bound = now + int(threshold_days * 86400)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, name, deadline_ts FROM food "
            "WHERE deadline_ts IS NOT NULL AND deadline_ts > 0 AND deadline_ts <= ? "
            "ORDER BY deadline_ts ASC",
            (upper_bound,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "name": (row["name"] or "").strip() or "Unnamed",
            "expiry": row["deadline_ts"] or 0,
        }
        for row in rows
    ]


def _row_to_entry(row: sqlite3.Row) -> FoodEntry:
    return FoodEntry(
        id=row["id"],
        name=row["name"] or "",
        image_path=row["image_path"],
        image_sha1=row["image_sha1"],
        status=row["status"] or "PENDING",
        deadline_ts=row["deadline_ts"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        sync_state=row["sync_state"] or "pending",
        attempt_count=row["attempt_count"] or 0,
        last_error=row["last_error"],
        upload_id=row["upload_id"],
        item_id=row["item_id"],
        class_id=row["class_id"],
        storage=row["storage"],
        storage_label=row["storage_label"],
        note=row["note"],
        ripeness=row["ripeness"],
        hours_left=row["hours_left"],
        last_synced_at=row["last_synced_at"],
        analysis_payload=row["analysis_payload"],
    )
