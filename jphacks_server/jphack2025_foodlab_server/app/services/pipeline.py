from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from openai import OpenAI
from openai.types.chat import ChatCompletion
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import FoodItem, FreshCheck
from app.utils.hashing import sha1_of_bytes

MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o")

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL") or None,
)

CLASS_MAP = {
    "napa_cabbage": "veg_napa_cabbage",
    "cabbage": "veg_cabbage",
    "eggplant": "veg_eggplant",
    "aubergine": "veg_eggplant",
    "tomato": "veg_tomato",
    "banana": "fruit_banana",
    "apple": "fruit_apple",
}


@dataclass
class AnalysisResult:
    upload_id: str
    name: str
    class_id: str
    confidence: float
    storage: str
    ripeness: str
    hours_left: int
    deadline: str
    note: str
    food_id: int
    check_id: Optional[int]
    raw_freshness: Dict[str, Any]


def map_to_class_id(label: str) -> str:
    key = label.strip().lower().replace(" ", "_")
    return CLASS_MAP.get(key, f"unknown::{key}")


def _sdk_has_responses() -> bool:
    return hasattr(client, "responses")


def call_llm_classify(image_bytes: bytes) -> Dict[str, Any]:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    prompt = (
        "You are a food item classifier. "
        "Identify the main ingredient in the image (e.g., napa_cabbage, eggplant, tomato, banana...). "
        'Return strict JSON: {"label": <snake_case_name>, "confidence": <0~1 float>}'
    )

    if _sdk_has_responses():
        schema = {
            "name": "food_item_schema",
            "schema": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["label", "confidence"],
                "additionalProperties": False,
            },
            "strict": True,
        }
        response = client.responses.create(
            model=MODEL_NAME,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_data": b64,
                            "mime_type": "image/jpeg",
                        },
                    ],
                }
            ],
            response_format={"type": "json_schema", "json_schema": schema},
        )
        text_payload = response.output_text
    else:
        chat: ChatCompletion = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        text_payload = chat.choices[0].message.content

    try:
        data = json.loads(text_payload)
        return {
            "label": str(data.get("label") or data.get("class") or "").strip(),
            "confidence": float(data.get("confidence") or 0.0),
        }
    except Exception:
        return {"label": "", "confidence": 0.0}


def call_llm_freshness(class_id: str, storage: str, image_hint: str) -> Dict[str, Any]:
    now_iso = datetime.now(timezone.utc).isoformat()
    prompt = f"""
You are a food freshness assessor. Today (UTC) is {now_iso}.
The item class is "{class_id}". Storage: "{storage}".
Return strict JSON with keys:
- ripeness (string; e.g. green/ripe/overripe)
- hours_left (integer; remaining safe hours under the given storage)
- deadline (ISO8601 string in UTC)
- note (short string)

Rules:
- hours_left >= 0. deadline = now + hours_left hours (UTC).
- Keep it practical and conservative.
- Output JSON only.
"""

    if _sdk_has_responses():
        schema = {
            "name": "fresh_schema",
            "schema": {
                "type": "object",
                "properties": {
                    "ripeness": {"type": "string"},
                    "hours_left": {"type": "integer"},
                    "deadline": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["ripeness", "hours_left", "deadline", "note"],
                "additionalProperties": False,
            },
            "strict": True,
        }
        response = client.responses.create(
            model=MODEL_NAME,
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            response_format={"type": "json_schema", "json_schema": schema},
            temperature=0.2,
        )
        text_payload = response.output_text
    else:
        chat = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        text_payload = chat.choices[0].message.content

    try:
        data = json.loads(text_payload)
        ripeness = str(data.get("ripeness", "")).strip() or "unknown"
        hours_left = int(data.get("hours_left", 0))
        deadline_raw = str(data.get("deadline", "")).strip()
        note = str(data.get("note", "")).strip()
        return {
            "ripeness": ripeness,
            "hours_left": hours_left,
            "deadline": deadline_raw,
            "note": note,
        }
    except Exception:
        fallback_deadline = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        return {
            "ripeness": "unknown",
            "hours_left": 12,
            "deadline": fallback_deadline,
            "note": "fallback",
        }


def _normalize_deadline(raw: str, hours_left: int) -> datetime:
    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc) + timedelta(hours=max(hours_left, 0))


def _get_note(raw_json: Dict[str, Any] | str) -> str:
    if isinstance(raw_json, dict):
        note = raw_json.get("note")
        if isinstance(note, str):
            return note
        return ""
    if isinstance(raw_json, str) and raw_json.strip():
        try:
            data = json.loads(raw_json)
            note = data.get("note")
            if isinstance(note, str):
                return note
        except Exception:
            return ""
    return ""


def _get_or_create_food(session: Session, upload_id: str, image_bytes: bytes) -> FoodItem:
    food = session.scalar(select(FoodItem).where(FoodItem.image_sha1 == upload_id).limit(1))
    if food is not None:
        return food

    cls_res = call_llm_classify(image_bytes)
    label = (cls_res.get("label") or "").strip()
    if not label:
        raise ValueError("unable to classify image")
    confidence = float(cls_res.get("confidence") or 0.0)
    class_id = map_to_class_id(label)

    food = FoodItem(
        image_sha1=upload_id,
        guess_label=label,
        class_id=class_id,
        confidence=confidence,
    )
    session.add(food)
    session.commit()
    session.refresh(food)
    return food


def analyze_image(image_bytes: bytes, storage: str, persist_check: bool) -> AnalysisResult:
    upload_id = sha1_of_bytes(image_bytes)

    with SessionLocal() as session:
        food = _get_or_create_food(session, upload_id, image_bytes)

        freshness = call_llm_freshness(food.class_id, storage, food.guess_label)
        hours_left = int(freshness.get("hours_left", 0))
        deadline_dt = _normalize_deadline(str(freshness.get("deadline", "")), hours_left)
        deadline_iso = deadline_dt.astimezone(timezone.utc).isoformat()
        ripeness = str(freshness.get("ripeness", "unknown"))
        note = _get_note(freshness)

        check_id: Optional[int] = None
        if persist_check:
            check = FreshCheck(
                food_id=food.id,
                storage=storage,
                ripeness=ripeness,
                hours_left=hours_left,
                deadline=deadline_dt,
                raw_json=json.dumps(freshness, ensure_ascii=False),
            )
            session.add(check)
            session.commit()
            session.refresh(check)
            check_id = check.id

        return AnalysisResult(
            upload_id=upload_id,
            name=food.guess_label,
            class_id=food.class_id,
            confidence=float(food.confidence),
            storage=storage,
            ripeness=ripeness,
            hours_left=hours_left,
            deadline=deadline_iso,
            note=note,
            food_id=food.id,
            check_id=check_id,
            raw_freshness=freshness,
        )
