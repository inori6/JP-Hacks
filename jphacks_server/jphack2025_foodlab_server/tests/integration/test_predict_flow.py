import os
from pathlib import Path

import pytest
import requests
from tenacity import RetryError, retry, retry_if_exception_type, stop_after_delay, wait_fixed

pytestmark = pytest.mark.integration

if os.getenv("RUN_INTEGRATION") != "1":
    pytest.skip("integration tests disabled; set RUN_INTEGRATION=1 to enable", allow_module_level=True)

API_BASE = os.getenv("API_BASE", "https://lab.160.16.126.35.sslip.io")
API_KEY = os.getenv("API_KEY", "")
ASSET_PATH = Path(__file__).parent.parent / "assets" / "apple.jpg"


def _headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if API_KEY:
        headers["X-Api-Key"] = API_KEY
    headers.setdefault("Accept", "application/json")
    return headers


class _JobPending(Exception):
    """Raised while job is still pending."""


@retry(
    stop=stop_after_delay(60),
    wait=wait_fixed(3),
    retry=retry_if_exception_type(_JobPending),
    reraise=True,
)
def _poll_job(job_id: str, headers: dict[str, str]) -> dict:
    response = requests.get(
        f"{API_BASE}/api/v1/jobs/{job_id}", headers=headers, timeout=30
    )
    response.raise_for_status()
    data = response.json()
    status = data.get("status")
    if status == "done":
        return data.get("result") or {}
    if status == "error":
        pytest.fail(f"Job error: {data.get('error')}")
    raise _JobPending()


def test_mobile_upload_and_analyze_flow():
    headers = _headers()

    with ASSET_PATH.open("rb") as handle:
        files = {"file": (ASSET_PATH.name, handle, "image/jpeg")}
        upload_resp = requests.post(
            f"{API_BASE}/api/v1/uploads",
            files=files,
            headers=headers,
            timeout=60,
        )
    upload_resp.raise_for_status()
    upload_data = upload_resp.json()

    upload_id = upload_data.get("upload_id")
    assert upload_id, "upload_id missing"

    analyze_resp = requests.post(
        f"{API_BASE}/api/v1/analyze",
        json={"upload_id": upload_id, "storage": "cool"},
        headers=headers,
        timeout=60,
    )

    if analyze_resp.status_code == 202:
        job_id = analyze_resp.json().get("job_id")
        assert job_id, "job_id missing in 202 response"
        try:
            result = _poll_job(job_id, headers)
        except RetryError as exc:  # noqa: PERF203 - explicit failure path
            pytest.fail(f"Job did not finish within timeout window: {exc}")
    else:
        analyze_resp.raise_for_status()
        result = analyze_resp.json()

    assert result.get("upload_id") == upload_id
    assert isinstance(result.get("name"), str)
    assert isinstance(result.get("class_id"), str)
    assert isinstance(result.get("confidence"), (float, int))
    assert isinstance(result.get("expiry"), str)
    assert isinstance(result.get("hours_left"), int)
    assert result.get("storage") == "cool"

    create_resp = requests.post(
        f"{API_BASE}/api/v1/items",
        json={"upload_id": upload_id, "storage": "cool"},
        headers=headers,
        timeout=60,
    )
    create_resp.raise_for_status()
    payload = create_resp.json()
    assert isinstance(payload.get("id"), int)
    assert isinstance(payload.get("food_id"), int)
    assert isinstance(payload.get("expiry"), str)
