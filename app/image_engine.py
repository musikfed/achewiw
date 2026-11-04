"""Optional REAL text-to-image engine via a private local ComfyUI server.

Unlike qwen2.5vl or granite-vision, ComfyUI with a compatible diffusion
checkpoint can CREATE a raster image. Ollama is not used for this generation.
No automatic unloading of models in the SHARED Ollama daemon.
"""
from __future__ import annotations
import asyncio
from io import BytesIO
import os
import random
from urllib.parse import urlsplit

import httpx

COMFYUI_URL = os.getenv("ACHEWIW_COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")


def engine_url() -> str:
    parts = urlsplit(COMFYUI_URL)
    if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Графический движок должен быть доступен только на loopback")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path:
        raise ValueError("Недопустимый адрес графического движка")
    return COMFYUI_URL


def extract_checkpoints(info: dict) -> list[str]:
    try:
        values = info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
    except (KeyError, IndexError, TypeError):
        return []
    return [name for name in values if isinstance(name, str) and name][:64]


def pick_checkpoint(checkpoints: list[str]) -> str:
    if not checkpoints:
        raise ValueError("В ComfyUI не установлен графический checkpoint")
    # Prefer SDXL if it is available; checkpoint names come from ComfyUI itself.
    return next((name for name in checkpoints if "sdxl" in name.lower() or
                 "sd_xl" in name.lower()), checkpoints[0])


async def status() -> dict:
    try:
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
            r = await client.get(engine_url() + "/object_info/CheckpointLoaderSimple")
            r.raise_for_status()
        checkpoints = extract_checkpoints(r.json())
        if not checkpoints:
            return {"available": False, "reason": "ComfyUI отвечает, но нет checkpoint-модели"}
        return {"available": True, "model": pick_checkpoint(checkpoints),
                "checkpoints": len(checkpoints), "engine": "ComfyUI / локальная генерация"}
    except (httpx.HTTPError, ValueError, KeyError):
        return {"available": False,
                "reason": "Локальный ComfyUI недоступен или не настроен. Vision-модели Ollama картинки не рисуют."}


def workflow(prompt: str, checkpoint: str, seed: int) -> dict:
    size = 1024 if "xl" in checkpoint.lower() else 512
    return {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {
            "text": "blurry, watermark, text, low quality, bad anatomy",
            "clip": ["4", 1]}},
        "7": {"class_type": "EmptyLatentImage", "inputs": {
            "width": size, "height": size, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": 24, "cfg": 7, "sampler_name": "euler",
            "scheduler": "normal", "denoise": 1,
            "model": ["4", 0], "positive": ["5", 0],
            "negative": ["6", 0], "latent_image": ["7", 0]}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["4", 2]}},
        "10": {"class_type": "SaveImage", "inputs": {
            "filename_prefix": "achewiw_local", "images": ["9", 0]}},
    }


async def generate(prompt: str) -> tuple[bytes, str]:
    if not prompt.strip() or len(prompt) > 2500:
        raise ValueError("Описание иллюстрации: от 1 до 2500 символов")
    address = engine_url()
    async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
        r = await client.get(address + "/object_info/CheckpointLoaderSimple")
        r.raise_for_status()
        ckpt = pick_checkpoint(extract_checkpoints(r.json()))
        r = await client.post(address + "/prompt", json={
            "prompt": workflow(prompt, ckpt, random.SystemRandom().randint(0, 2**32 - 1)),
            "client_id": "achewiw-studio",
        })
        r.raise_for_status()
        task_id = r.json().get("prompt_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("ComfyUI не вернул идентификатор генерации")
        # ComfyUI keeps the job on its own server; no host process management.
        for attempt in range(90):
            await asyncio.sleep(2)
            history = await client.get(address + "/history/" + task_id)
            history.raise_for_status()
            job = history.json().get(task_id, {})
            images = job.get("outputs", {}).get("10", {}).get("images", [])
            if images:
                details = images[0]
                filename = details.get("filename")
                if not isinstance(filename, str) or not filename:
                    raise ValueError("Движок не вернул файл изображения")
                file_response = await client.get(address + "/view", params={
                    "filename": filename, "subfolder": details.get("subfolder", ""),
                    "type": details.get("type", "output"),
                })
                file_response.raise_for_status()
                if len(file_response.content) > 12 * 1024 * 1024:
                    raise ValueError("Изображение ComfyUI слишком велико")
                return file_response.content, ckpt
            if job.get("status", {}).get("status_str") == "error":
                raise ValueError("ComfyUI завершил генерацию с ошибкой")
    raise TimeoutError("ComfyUI не закончил генерацию за 3 минуты")

async def free_cached_models() -> str:
    """Optional /free on loopback ComfyUI. Never clear external queued jobs.

    ComfyUI can be a shared server: require explicit opt-in in the UI and
    skip if any request is running or pending. This call only addresses ComfyUI,
    not Ollama or a global CUDA reset.
    """
    try:
        async with httpx.AsyncClient(
            timeout=6.0, trust_env=False
        ) as client:
            queue = await client.get(engine_url() + "/queue")
            queue.raise_for_status()
            state = queue.json()
            if state.get("queue_running") or state.get("queue_pending"):
                return "skipped_comfyui_queue_busy"
            response = await client.post(engine_url() + "/free", json={
                "unload_models": True, "free_memory": True,
            })
            response.raise_for_status()
            return "free_requested"
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return "free_request_failed"
