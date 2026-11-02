"""Safe host snapshot reader and Ollama response summarizer for ACHEWIW.

Host collector writes data/monitor/host.json; web container only reads it.
Never run nvidia-smi, systemctl or Docker from the FastAPI container.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

MAX_SNAPSHOT_SIZE = 32_768
MAX_AGE_SECONDS = 30


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _bounded_number(value, minimum=0, maximum=1e18):
    return value if _finite_number(value) and minimum <= value <= maximum else None


def _safe_card(card):
    if not isinstance(card, dict):
        return None
    gpu_name = card.get("name")
    if not isinstance(gpu_name, str):
        gpu_name = "GPU"
    gpu = {
        "index": _bounded_number(card.get("index"), 0, 128),
        "name": gpu_name[:100],
        "utilization_percent": _bounded_number(card.get("utilization_percent"), 0, 100),
        "memory_used_bytes": _bounded_number(card.get("memory_used_bytes")),
        "memory_total_bytes": _bounded_number(card.get("memory_total_bytes")),
        "temperature_c": _bounded_number(card.get("temperature_c"), -50, 150),
    }
    return gpu


def load_snapshot(path: Path, now: float | None = None) -> dict:
    """Return only a typed metrics allow-list. Never return host file paths or raw errors."""
    try:
        if path.is_symlink() or not path.is_file():
            return {"status": "unavailable"}
        if path.stat().st_size > MAX_SNAPSHOT_SIZE:
            return {"status": "invalid"}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return {"status": "unavailable"}
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return {"status": "invalid"}
    stamp = data.get("captured_at")
    current = time.time() if now is None else now
    if not _finite_number(stamp) or stamp > current + 5:
        return {"status": "invalid"}
    if current - stamp > MAX_AGE_SECONDS:
        return {"status": "stale", "age_seconds": round(current - stamp)}
    cpu, memory = data.get("cpu"), data.get("memory")
    if not isinstance(cpu, dict) or not isinstance(memory, dict):
        return {"status": "invalid"}
    cards = data.get("gpus")
    if not isinstance(cards, list):
        cards = []
    gpu_cards = [c for item in cards[:8] if (c := _safe_card(item)) is not None]
    return {
        "status": "ok",
        "age_seconds": round(max(0, current - stamp)),
        "cpu": {
            "usage_percent": _bounded_number(cpu.get("usage_percent"), 0, 100),
            "logical_processors": _bounded_number(cpu.get("logical_processors"), 1, 4096),
            "load_1": _bounded_number(cpu.get("load_1")),
            "load_5": _bounded_number(cpu.get("load_5")),
        },
        "memory": {
            "usage_percent": _bounded_number(memory.get("usage_percent"), 0, 100),
            "used_bytes": _bounded_number(memory.get("used_bytes")),
            "total_bytes": _bounded_number(memory.get("total_bytes")),
        },
        "gpu_status": "ok" if data.get("gpu_status") == "ok" else "unavailable",
        "gpus": gpu_cards,
    }


def summarize_ollama_running(data: object) -> list[dict]:
    """Expose names and VRAM usage only, never raw model metadata or prompts."""
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return []
    answer = []
    for model in data["models"][:24]:
        if not isinstance(model, dict):
            continue
        name = model.get("name")
        if not isinstance(name, str) or not name:
            continue
        answer.append({
            "name": name[:160],
            "vram_bytes": _bounded_number(model.get("size_vram")),
        })
    return answer
