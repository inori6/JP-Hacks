"""HTTP client for the Lab backend with logging and job polling."""

from __future__ import annotations

import json
import mimetypes
import socket
import time
from pathlib import Path
from typing import Any, Dict, NamedTuple, Optional
from urllib.parse import urlparse

import certifi
import httpx

from .logger import log_event, log_http


class HealthCheckResult(NamedTuple):
    category: str
    status_code: Optional[int]
    elapsed_ms: float
    detail: Optional[str] = None
    host: Optional[str] = None



class ApiError(RuntimeError):
    pass


class ApiClient:
    def __init__(self, base_url: str, api_key: str, user_data_dir: str | Path):
        base = _normalize_base_url(base_url)
        self._base_url = base
        self._api_base = f"{base}/api/v1"
        self._api_key = (api_key or "").strip()
        self._user_data_dir = str(user_data_dir)

    def configure(self, base_url: str, api_key: str) -> None:
        base = _normalize_base_url(base_url)
        self._base_url = base
        self._api_base = f"{base}/api/v1"
        self._api_key = (api_key or "").strip()

    # ---- public API -----------------------------------------------------

    def health_check(self, *, with_api_key: bool = True) -> HealthCheckResult:
        url = f"{self._api_base}/healthz"
        parsed = urlparse(self._base_url)
        host = parsed.hostname or self._base_url
        start = time.perf_counter()

        try:
            socket.getaddrinfo(host, 443)
        except socket.gaierror as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._log_health(host, "dns", elapsed_ms, str(exc))
            return HealthCheckResult("dns", None, elapsed_ms, str(exc), host)
        except Exception as exc:  # pragma: no cover - defensive
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._log_health(host, "dns", elapsed_ms, str(exc))
            return HealthCheckResult("dns", None, elapsed_ms, str(exc), host)

        try:
            response = self._request(
                "GET",
                "/healthz",
                include_api_key=with_api_key,
                timeout=httpx.Timeout(10.0),
            )
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._log_health(host, "ok", elapsed_ms, status=response.status_code)
            return HealthCheckResult("ok", response.status_code, elapsed_ms, host=host)
        except httpx.HTTPStatusError as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            status = exc.response.status_code
            category = "server"
            if status == 401:
                category = "unauth"
            elif 400 <= status < 500:
                category = "network"
            self._log_health(host, category, elapsed_ms, exc.response.text[:200], status=status)
            return HealthCheckResult(category, status, elapsed_ms, exc.response.text[:200], host)
        except httpx.TimeoutException as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._log_health(host, "timeout", elapsed_ms, str(exc))
            return HealthCheckResult("timeout", None, elapsed_ms, str(exc), host)
        except httpx.RequestError as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._log_health(host, "network", elapsed_ms, str(exc))
            return HealthCheckResult("network", None, elapsed_ms, str(exc), host)
        except Exception as exc:  # pragma: no cover - defensive
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._log_health(host, "error", elapsed_ms, str(exc))
            return HealthCheckResult("error", None, elapsed_ms, str(exc), host)

    def upload_image(self, path: Path, storage: str = "cool") -> Dict[str, Any]:
        mime_type, _ = mimetypes.guess_type(path.name)
        mime_type = mime_type or "image/jpeg"
        data = {"storage": storage}
        with path.open("rb") as fh:
            files = {"file": (path.name, fh, mime_type)}
            response = self._request("POST", "uploads", data=data, files=files)
        return _json(response)

    def analyze_upload(self, upload_id: str, *, mode: str = "sync", poll_timeout: float = 120.0) -> Dict[str, Any]:
        payload = {"upload_id": upload_id, "mode": mode}
        response = self._request("POST", "analyze", json=payload)
        data = _json(response)
        status = data.get("status")
        if status in {"queued", "pending"} and data.get("job_id"):
            job_id = data["job_id"]
            return self._wait_for_job(job_id, timeout=poll_timeout)
        if status == "done" and data.get("result"):
            return data["result"]
        return data

    def _wait_for_job(self, job_id: str, *, timeout: float) -> Dict[str, Any]:
        start = time.monotonic()
        while True:
            response = self._request("GET", f"jobs/{job_id}")
            data = _json(response)
            status = data.get("status")
            if status == "done" and data.get("result"):
                return data["result"]
            if status == "failed":
                raise ApiError(f"job {job_id} failed: {data.get('error')}")
            if time.monotonic() - start > timeout:
                raise ApiError(f"job {job_id} timed out")
            time.sleep(1.5)

    def create_item(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = self._request("POST", "items", json=payload)
        return _json(response)

    # ---- internals ------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        include_api_key: bool = True,
        **kwargs,
    ) -> httpx.Response:
        url = self._resolve_url(path)
        headers = kwargs.pop("headers", {}) or {}
        if include_api_key and self._api_key:
            headers.setdefault("X-Api-Key", self._api_key)
        timeout = kwargs.pop("timeout", httpx.Timeout(30.0))
        if not isinstance(timeout, httpx.Timeout):
            timeout = httpx.Timeout(timeout)
        start = time.perf_counter()
        status_label = "ERR"
        note = ""
        try:
            with httpx.Client(http2=False, verify=certifi.where(), timeout=timeout) as client:
                response = client.request(method, url, headers=headers, **kwargs)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            status_label = str(response.status_code)
            log_http(self._user_data_dir, method, url, status_label, elapsed_ms)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            status_label = str(exc.response.status_code)
            note = exc.response.text[:200]
            log_http(self._user_data_dir, method, url, status_label, elapsed_ms, note)
            raise
        except Exception as exc:  # pragma: no cover - network stack
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            note = str(exc)
            log_http(self._user_data_dir, method, url, status_label, elapsed_ms, note)
            raise

    def _log_health(self, host: str, result: str, elapsed_ms: float, note: Optional[str] = None, *, status: Optional[int] = None) -> None:
        suffix = f" | status={status}" if status is not None else ""
        detail = f" | note={note}" if note else ""
        log_event(
            self._user_data_dir,
            f"HEALTHCHECK | host={host} | res={result} | ms={elapsed_ms:.0f}{suffix}{detail}",
        )

    def _resolve_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        if path.startswith("/api/"):
            return f"{self._base_url}{path}"
        if path.startswith("/"):
            return f"{self._api_base}{path}"
        return f"{self._api_base}/{path.lstrip('/')}"


def _normalize_base_url(url: str) -> str:
    url = (url or "").strip().rstrip('/')
    if not url:
        raise ValueError("base_url is required")
    return url.rstrip("/")


def _json(response: httpx.Response) -> Dict[str, Any]:
    try:
        return response.json()
    except json.JSONDecodeError as exc:  # pragma: no cover - backend bug
        raise ApiError("invalid JSON response") from exc
