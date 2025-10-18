from __future__ import annotations

import hashlib


def sha1_of_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()

