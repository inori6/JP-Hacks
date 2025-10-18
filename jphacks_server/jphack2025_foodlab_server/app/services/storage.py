from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from app.config import settings
from app.utils.hashing import sha1_of_bytes


class StorageService:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _dir_for(self, upload_id: str) -> Path:
        prefix = upload_id[:2] if len(upload_id) >= 2 else upload_id
        return self.root / prefix

    def path_for(self, upload_id: str) -> Path:
        return self._dir_for(upload_id) / upload_id

    def save(self, data: bytes, filename: Optional[str] = None) -> Tuple[str, Path]:
        upload_id = sha1_of_bytes(data)
        directory = self._dir_for(upload_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / upload_id
        path.write_bytes(data)
        return upload_id, path

    def read(self, upload_id: str) -> bytes:
        path = self.path_for(upload_id)
        if not path.exists():
            raise FileNotFoundError(upload_id)
        return path.read_bytes()

    def exists(self, upload_id: str) -> bool:
        return self.path_for(upload_id).exists()

    def public_url(self, upload_id: str) -> Optional[str]:
        base = settings.upload_url_base
        if not base:
            return None
        return f"{base.rstrip('/')}/{upload_id}"


storage = StorageService(settings.storage_dir)

