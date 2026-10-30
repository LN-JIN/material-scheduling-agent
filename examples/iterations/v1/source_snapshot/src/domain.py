"""Trusted input validation and time helpers (Python standard library only)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


class ValidationError(ValueError):
    pass


def parse_time(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValidationError("time must be an ISO 8601 string with a timezone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("invalid ISO 8601 time") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValidationError("time must include a timezone")
    return result


def iso(value: datetime) -> str:
    return value.isoformat()


def same_time(left: str, right: str) -> bool:
    return parse_time(left) == parse_time(right)


def validate_request(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValidationError("request must be an object")
    data = dict(payload)
    for key in ("request_id", "material_id"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValidationError(f"{key} is required and must be a nonempty string")
        if len(data[key]) > 200:
            raise ValidationError(f"{key} is too long")
    parse_time(data.get("requested_time"))
    if data.get("time_requirement") not in ("fixed", "window"):
        raise ValidationError("time_requirement must be fixed or window")
    if data["time_requirement"] == "window":
        begin = parse_time(data.get("allowed_start"))
        end = parse_time(data.get("allowed_end"))
        if begin >= end:
            raise ValidationError("allowed_start must be before allowed_end")
    if "note" in data and not isinstance(data["note"], str):
        raise ValidationError("note must be a string")
    return data


def payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
