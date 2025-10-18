from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.errors import api_error, format_error_payload
from app.config import settings
from app.security import RequestContext, get_request_context
from app.services.pipeline import MODEL_NAME, AnalysisResult, analyze_image
from app.services.storage import storage
from app.utils.jobs import job_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["mobile"])

ALLOWED_STORAGES = {"cool", "room", "freeze"}

ANALYZE_EXAMPLE = {
    "upload_id": "4bd2d4a60f0f3bd9...",
    "name": "apple",
    "class_id": "fruit_apple",
    "confidence": 0.93,
    "storage": "cool",
    "ripeness": "ripe",
    "hours_left": 72,
    "expiry": "2024-09-28T12:33:45+00:00",
    "note": "Keep refrigerated and consume within 3 days",
    "food_id": 123,
    "check_id": None,
}


class AnalyzeJSONPayload(BaseModel):
    upload_id: str = Field(..., min_length=8, examples=["4bd2d4..."])
    storage: str = Field(default="cool", examples=["cool"])


class CreateItemPayload(BaseModel):
    upload_id: str = Field(..., min_length=8, examples=["4bd2d4..."])
    storage: str = Field(default="cool", examples=["cool"])


class UploadResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "upload_id": "4bd2d4a60f0f3bd9...",
            "filename": "apple.jpg",
            "mime_type": "image/jpeg",
            "size": 348231,
            "url": "https://cdn.example.com/uploads/4b/4bd2d4a60f0f3bd9...",
        }
    })

    upload_id: str
    filename: Optional[str]
    mime_type: Optional[str]
    size: int
    url: Optional[str] = None


class AnalyzeResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": ANALYZE_EXAMPLE})

    upload_id: str
    name: str
    class_id: str
    confidence: float
    storage: str
    ripeness: str
    hours_left: int
    expiry: str
    note: str
    food_id: int
    check_id: Optional[int]


class JobAcceptedResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"job_id": "0e7c0c...", "status": "pending"}})

    job_id: str
    status: Literal["pending"]


class JobDetailResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "job_id": "0e7c0c...",
                    "status": "done",
                    "result": ANALYZE_EXAMPLE,
                    "error": None,
                },
                {
                    "job_id": "0e7c0c...",
                    "status": "error",
                    "result": None,
                    "error": "Timed out contacting model",
                },
            ]
        }
    )

    job_id: str
    status: Literal["pending", "done", "error"]
    result: Optional[AnalyzeResponse]
    error: Optional[str]


class CreateItemResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {"id": 456, "food_id": 123, "expiry": "2024-09-28T12:33:45+00:00"}
    })

    id: int
    food_id: int
    expiry: str


class HealthResponse(BaseModel):
    status: str
    model: str


def normalize_storage(value: Optional[str]) -> str:
    storage = (value or "cool").strip().lower()
    if storage not in ALLOWED_STORAGES:
        allowed = ", ".join(sorted(ALLOWED_STORAGES))
        raise api_error(422, "invalid_storage", f"storage must be one of: {allowed}")
    return storage


def ensure_image(file: UploadFile) -> None:
    content_type = (file.content_type or "").lower()
    if not content_type.startswith("image/"):
        raise api_error(415, "unsupported_media_type", "Only image uploads are allowed")


def validate_size(data: bytes) -> None:
    if len(data) > settings.max_upload_bytes:
        raise api_error(
            413,
            "file_too_large",
            f"File exceeds limit of {settings.max_upload_mb} MB",
        )
    if not data:
        raise api_error(422, "empty_file", "Uploaded file is empty")


def to_response_payload(result: AnalysisResult) -> Dict[str, Any]:
    return {
        "upload_id": result.upload_id,
        "name": result.name,
        "class_id": result.class_id,
        "confidence": result.confidence,
        "storage": result.storage,
        "ripeness": result.ripeness,
        "hours_left": result.hours_left,
        "expiry": result.deadline,
        "note": result.note,
        "food_id": result.food_id,
        "check_id": result.check_id,
    }


async def _run_analysis(image_bytes: bytes, storage_value: str, persist: bool) -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    try:
        result: AnalysisResult = await loop.run_in_executor(
            None, lambda: analyze_image(image_bytes, storage_value, persist)
        )
    except ValueError as exc:
        raise api_error(422, "analysis_failed", str(exc)) from exc
    return to_response_payload(result)


async def mobile_http_exception_handler(request: Request, exc: HTTPException):
    payload = format_error_payload(exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"error": payload})


async def mobile_general_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "unknown")
    logger.exception("Unhandled mobile API error [request_id=%s]", request_id, exc_info=exc)
    payload = {"code": "internal_error", "message": "Internal server error"}
    return JSONResponse(status_code=500, content={"error": payload})


@router.post("/uploads", status_code=201, response_model=UploadResponse)
async def create_upload(
    context: RequestContext = Depends(get_request_context),
    file: UploadFile = File(...),
):
    ensure_image(file)
    data = await file.read()
    validate_size(data)

    upload_id, _ = storage.save(data, file.filename)
    url = storage.public_url(upload_id)
    return {
        "upload_id": upload_id,
        "filename": file.filename,
        "mime_type": file.content_type,
        "size": len(data),
        "url": url,
    }


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    responses={202: {"model": JobAcceptedResponse, "description": "Job queued"}},
)
async def analyze(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    file: UploadFile | None = File(default=None),
    upload_id_form: Optional[str] = Form(default=None),
    storage_form: str = Form(default="cool"),
):
    storage_value = "cool"
    image_bytes: Optional[bytes] = None
    upload_id: Optional[str] = None
    request_id = getattr(request.state, "request_id", "unknown")

    content_type = (request.headers.get("content-type") or "").lower()
    if content_type.startswith("application/json"):
        payload = AnalyzeJSONPayload.model_validate(await request.json())
        upload_id = payload.upload_id
        storage_value = normalize_storage(payload.storage)
        try:
            image_bytes = storage.read(upload_id)
        except FileNotFoundError:
            raise api_error(404, "upload_not_found", "Upload not found; please upload first")
    else:
        storage_value = normalize_storage(storage_form)
        if upload_id_form:
            upload_id = upload_id_form.strip()
            try:
                image_bytes = storage.read(upload_id)
            except FileNotFoundError:
                raise api_error(404, "upload_not_found", "Upload not found; please upload first")
        elif file is not None:
            ensure_image(file)
            data = await file.read()
            validate_size(data)
            upload_id, _ = storage.save(data, file.filename)
            image_bytes = data
        else:
            raise api_error(422, "invalid_request", "Provide either upload_id or file")

    assert image_bytes is not None

    try:
        payload = await asyncio.wait_for(
            _run_analysis(image_bytes, storage_value, persist=False),
            timeout=settings.request_timeout_seconds,
        )
        payload.update({"upload_id": upload_id})
        return payload
    except asyncio.TimeoutError:
        job = job_store.create()

        async def background_job():
            try:
                result = await _run_analysis(image_bytes, storage_value, persist=False)
                result.update({"upload_id": upload_id})
                job_store.complete(job.id, result)
            except Exception as exc:  # noqa: BLE001 - handled for job tracking
                logger.exception(
                    "Background analysis failed [job_id=%s request_id=%s]",
                    job.id,
                    request_id,
                    exc_info=exc,
                )
                job_store.fail(job.id, str(exc))

        asyncio.create_task(background_job())
        return JSONResponse(status_code=202, content=JobAcceptedResponse(job_id=job.id, status="pending").model_dump())


@router.get("/jobs/{job_id}", response_model=JobDetailResponse)
async def get_job(job_id: str, context: RequestContext = Depends(get_request_context)):
    job = job_store.get(job_id)
    if not job:
        raise api_error(404, "job_not_found", "Job not found")
    return JobDetailResponse(job_id=job.id, status=job.status, result=job.result, error=job.error)


@router.post("/items", status_code=201, response_model=CreateItemResponse)
async def create_item(
    payload: CreateItemPayload,
    context: RequestContext = Depends(get_request_context),
):
    storage_value = normalize_storage(payload.storage)
    try:
        image_bytes = storage.read(payload.upload_id)
    except FileNotFoundError:
        raise api_error(404, "upload_not_found", "Upload not found; please upload first")

    result = await _run_analysis(image_bytes, storage_value, persist=True)
    if result.get("check_id") is None:
        raise api_error(500, "persist_failed", "Failed to persist item")
    return CreateItemResponse(id=result["check_id"], food_id=result["food_id"], expiry=result["expiry"])


@router.get("/healthz", response_model=HealthResponse)
async def healthz(context: RequestContext = Depends(get_request_context)):
    return {"status": "ok", "model": MODEL_NAME}
