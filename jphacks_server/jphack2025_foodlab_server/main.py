from __future__ import annotations

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.api.mobile import (
    mobile_general_exception_handler,
    mobile_http_exception_handler,
    router as mobile_router,
)
from app.config import settings
from app.middleware import RequestIDMiddleware
from app.services.pipeline import MODEL_NAME, AnalysisResult, analyze_image

app = FastAPI(title="AI Food Recognizer (OpenAI)", version="0.2.0")

app.add_middleware(RequestIDMiddleware)

app.add_exception_handler(HTTPException, mobile_http_exception_handler)
app.add_exception_handler(Exception, mobile_general_exception_handler)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(mobile_router)


@app.get("/health")
def health():
    return {"ok": True, "model": MODEL_NAME}


@app.post("/recognize/fresh")
def recognize_fresh(
    image: UploadFile = File(...),
    storage: str = Form("cool"),
):
    raw = image.file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="空文件")

    try:
        result: AnalysisResult = analyze_image(raw, storage=storage, persist_check=True)
    except ValueError as exc:  # 分类失败
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 返回泛化错误
        raise HTTPException(status_code=500, detail="分析失败") from exc

    return {
        "class_raw": result.name,
        "class_id": result.class_id,
        "confidence": result.confidence,
        "storage": result.storage,
        "ripeness": result.ripeness,
        "hours_left": result.hours_left,
        "deadline": result.deadline,
        "note": result.note,
    }


__all__ = ["app"]
