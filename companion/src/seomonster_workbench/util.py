"""Deterministic serialization and identifier helpers."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any


def canonical_json(value: Any) -> str:
    """Serialize a JSON value in the stable form used for hashes and exports."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def finite_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}_{digest(list(parts))[:24]}"


def json_value(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)
