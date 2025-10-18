from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock
from typing import Deque, Dict

from fastapi import Header, Request

from app.api.errors import api_error
from app.config import settings


@dataclass
class RequestContext:
    api_key: str | None
    client_ip: str
    rate_identifier: str


class RateLimiter:
    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max_per_minute
        self._events: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, identifier: str) -> None:
        if self.max_per_minute <= 0:
            return
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            bucket = self._events[identifier]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_per_minute:
                raise api_error(429, "rate_limit", "Too many requests; slow down")
            bucket.append(now)


rate_limiter = RateLimiter(settings.rate_limit_per_minute)
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def get_request_context(request: Request, x_api_key: str | None = Header(default=None)) -> RequestContext:
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    client_ip = forwarded or (request.client.host if request.client else "unknown")

    api_key = x_api_key
    if settings.api_keys:
        if not api_key or api_key not in settings.api_key_set:
            raise api_error(401, "unauthorized", "Invalid or missing API key")
    else:
        if client_ip not in LOCAL_HOSTS:
            raise api_error(401, "unauthorized", "API key required from remote clients")

    rate_key = f"{api_key or 'anon'}:{client_ip}"
    rate_limiter.hit(rate_key)

    return RequestContext(api_key=api_key, client_ip=client_ip, rate_identifier=rate_key)
