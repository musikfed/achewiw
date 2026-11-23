"""R3.8: strict parsing of untrusted local Ollama vision output.

The model supplies unverified suggestions, never approved diagrams or document edits.
"""
from __future__ import annotations

import json
import math
import re

KINDS = {"rectangle", "ellipse", "line", "arrow", "text"}
MAX_SUGGESTIONS = 60


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def parse_vision_reply(raw: str, *, width: int, height: int) -> list[dict]:
    if not isinstance(raw, str) or not 0 < len(raw) <= 30000:
        raise ValueError("Vision вернула пустой или чрезмерно длинный ответ")
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^\`\`\`(?:json)?\\s*", "", raw, count=1, flags=re.I)
        raw = re.sub(r"\\s*\`\`\`$", "", raw, count=1)
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("Vision не вернула корректный JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("proposals"), list):
        raise ValueError("В JSON отсутствует массив proposals")
    proposals = payload["proposals"]
    if len(proposals) > MAX_SUGGESTIONS:
        raise ValueError("Vision предложила слишком много фигур")
    clean = []
    for item in proposals:
        if not isinstance(item, dict) or item.get("type") not in KINDS:
            continue  # Unsupported model guesses must never enter the canvas.
        kind = item["type"]
        coords = [item.get(key) for key in ("x", "y", "w", "h")]
        if not all(_number(n) for n in coords):
            continue
        x, y, w, h = [round(float(n), 2) for n in coords]
        # Require bounded positive boxes. Lines may be horizontal/vertical, not reversed.
        if x < 0 or y < 0 or w < 0 or h < 0 or x + w > width or y + h > height:
            continue
        if kind in ("rectangle", "ellipse") and (w < 2 or h < 2):
            continue
        if kind in ("line", "arrow") and math.hypot(w, h) < 2:
            continue
        confidence = item.get("confidence", 0)
        if not _number(confidence) or not 0 <= confidence <= 1:
            confidence = 0
        result = {
            "id": "candidate-" + str(len(clean) + 1),
            "type": kind, "confidence": float(confidence),
            "verification": "unverified",
            "x": x, "y": y, "w": w, "h": h,
        }
        if kind == "text":
            value = item.get("text")
            if not isinstance(value, str) or not value.strip():
                continue
            result["text"] = value[:180]
        clean.append(result)
    return clean


def vision_prompt(width: int, height: int) -> str:
    return (
        "Analyze the provided technical diagram as a potentially unreliable image. "
        "Return ONLY a JSON object with key proposals, an array (maximum 60) of "
        "visible items. Each item: type (rectangle|ellipse|line|arrow|text), "
        "x,y,w,h as absolute pixel coordinates, confidence between 0 and 1. "
        "For text also include text. The image dimensions are "
        + str(width) + "x" + str(height) + ". "
        "Never invent hidden labels, edges, symbols or relations. "
        "Omit objects you cannot locate. Do not return markdown. "
        "Boxes and lines have nonnegative widths and heights. "
        "All coordinates must stay within the image."
    )
