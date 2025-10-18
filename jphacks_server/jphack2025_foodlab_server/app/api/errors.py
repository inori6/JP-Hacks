from __future__ import annotations

from fastapi import HTTPException


def api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def format_error_payload(detail: object) -> dict[str, str]:
    if isinstance(detail, dict):
        code = str(detail.get("code", "error"))
        message = str(detail.get("message", ""))
        return {"code": code, "message": message}
    return {"code": "error", "message": str(detail)}

