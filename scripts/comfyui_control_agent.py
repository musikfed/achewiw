#!/usr/bin/env python3
"""Loopback-only, token-protected ACHEWIW control bridge for the host ComfyUI script."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid
from urllib.parse import urlsplit

ROOT = Path("/1tbdisk/achewiw")
DATA_DIR = ROOT / "data"
SCRIPT = ROOT / "scripts" / "comfyui-local.sh"
TOKEN_FILE = DATA_DIR / "comfyui-control.token"
JOBS_DIR = DATA_DIR / "comfyui-control-jobs"
BASH = "/usr/bin/bash"
HOST = "127.0.0.1"
PORT = 8190
MAX_BODY = 2048
LOG_TAIL_BYTES = 6000
ACTIONS = {"install", "model-sdxl", "start", "stop"}
LOCK = threading.RLock()
JOBS: dict[str, dict] = {}


def validate_layout() -> None:
    if ROOT.is_symlink() or not ROOT.is_dir():
        raise RuntimeError("Недопустимый корень ACHEWIW")
    if DATA_DIR.is_symlink() or not DATA_DIR.is_dir():
        raise RuntimeError("Недопустимый каталог data/")
    scripts_dir = ROOT / "scripts"
    if scripts_dir.is_symlink() or not scripts_dir.is_dir():
        raise RuntimeError("Недопустимый каталог scripts/")
    if SCRIPT.is_symlink() or not SCRIPT.is_file():
        raise RuntimeError("Не найден собственный scripts/comfyui-local.sh")
    if TOKEN_FILE.is_symlink() or not TOKEN_FILE.is_file():
        raise RuntimeError("Не найден закрытый токен управления")


def read_token() -> str:
    validate_layout()
    mode = TOKEN_FILE.stat().st_mode & 0o777
    if mode & 0o077:
        raise RuntimeError("Небезопасные права файла токена; требуется chmod 600")
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if len(token) < 40:
        raise RuntimeError("Некорректный токен управления")
    return token


def _tail(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        return ""
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - LOG_TAIL_BYTES))
        return stream.read(LOG_TAIL_BYTES).decode("utf-8", errors="replace").strip()


def _active_job() -> dict | None:
    for job_id, job in reversed(list(JOBS.items())):
        if job["process"].poll() is None:
            return {"job_id": job_id, "action": job["action"]}
    return None


def parse_status(output: str, returncode: int = 0) -> dict:
    return {
        "ok": returncode == 0,
        "installed": "ComfyUI установлен в " in output,
        "model_present": "SDXL присутствует" in output,
        "api_ready": "API ComfyUI на localhost:8188 отвечает" in output,
        "owned_running": "Наш ComfyUI работает PID=" in output,
        "port_busy": "Порт 8188 занят" in output,
    }


def status_snapshot() -> dict:
    validate_layout()
    try:
        result = subprocess.run(
            [BASH, str(SCRIPT), "doctor"],
            cwd=str(ROOT), capture_output=True, text=True,
            timeout=20, check=False,
        )
        payload = parse_status((result.stdout or "") + "\n" + (result.stderr or ""),
                               result.returncode)
    except subprocess.TimeoutExpired:
        payload = {"ok": False, "reason": "Проверка ComfyUI превысила 20 секунд"}
    except OSError as exc:
        payload = {"ok": False, "reason": "Не удалось проверить ComfyUI: " + str(exc)}
    with LOCK:
        active = _active_job()
    payload.update({
        "busy": active is not None,
        "active_job_id": active["job_id"] if active else None,
        "active_action": active["action"] if active else None,
    })
    if not payload.get("reason"):
        if payload.get("api_ready"):
            payload["summary"] = "ComfyUI отвечает на localhost:8188"
        elif payload.get("port_busy"):
            payload["summary"] = "Порт 8188 занят неидентифицированным процессом"
        elif not payload.get("installed"):
            payload["summary"] = "ComfyUI ещё не установлен"
        elif not payload.get("model_present"):
            payload["summary"] = "SDXL checkpoint ещё не загружен на диск"
        else:
            payload["summary"] = "ComfyUI остановлен; SDXL установлен"
    return payload


def start_job(action: str) -> dict:
    if not isinstance(action, str) or action not in ACTIONS:
        raise ValueError("Недопустимое действие ComfyUI")
    validate_layout()
    if JOBS_DIR.is_symlink():
        raise RuntimeError("Каталог журналов управления не должен быть ссылкой")
    JOBS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not JOBS_DIR.is_dir() or JOBS_DIR.is_symlink():
        raise RuntimeError("Недопустимый каталог журналов управления")
    with LOCK:
        active = _active_job()
        if active:
            raise RuntimeError("Уже выполняется действие ComfyUI: " + active["action"])
        job_id = uuid.uuid4().hex
        log_path = JOBS_DIR / (job_id + ".log")
        with log_path.open("xb") as log:
            process = subprocess.Popen(
                [BASH, str(SCRIPT), action],
                cwd=str(ROOT), stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, close_fds=True,
            )
        JOBS[job_id] = {
            "action": action, "process": process,
            "log_path": log_path, "started_at": time.time(),
        }
        completed = [key for key, value in JOBS.items()
                     if value["process"].poll() is not None]
        while len(JOBS) > 40 and completed:
            old = completed.pop(0)
            old_job = JOBS.pop(old, None)
            if old_job:
                try:
                    old_job["log_path"].unlink(missing_ok=True)
                except OSError:
                    pass
        return {"job_id": job_id, "action": action, "status": "running"}


def get_job(job_id: str) -> dict | None:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        return None
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        returncode = job["process"].poll()
        status = "running" if returncode is None else (
            "succeeded" if returncode == 0 else "failed"
        )
        return {
            "job_id": job_id, "action": job["action"], "status": status,
            "returncode": returncode, "output": _tail(job["log_path"]),
            "started_at": job["started_at"],
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "ACHEWIW-ComfyUI-Control/1"

    def log_message(self, _format: str, *args) -> None:
        return

    def send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        try:
            expected = read_token()
        except (OSError, RuntimeError):
            return False
        supplied = self.headers.get("X-ACHEWIW-Control-Token", "")
        return bool(supplied) and hmac.compare_digest(supplied, expected)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if not self.authorized():
            self.send_json(403, {"detail": "Доступ к host-control запрещён"})
            return
        if path == "/status":
            try:
                self.send_json(200, status_snapshot())
            except (OSError, RuntimeError) as exc:
                self.send_json(503, {"detail": str(exc)})
            return
        match = re.fullmatch(r"/jobs/([0-9a-f]{32})", path)
        if match:
            job = get_job(match.group(1))
            if job is None:
                self.send_json(404, {"detail": "Задача управления не найдена"})
            else:
                self.send_json(200, job)
            return
        self.send_json(404, {"detail": "Маршрут не найден"})

    def do_POST(self) -> None:
        if not self.authorized():
            self.send_json(403, {"detail": "Доступ к host-control запрещён"})
            return
        if urlsplit(self.path).path != "/control":
            self.send_json(404, {"detail": "Маршрут не найден"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY:
                raise ValueError("Недопустимый размер запроса")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or set(payload) != {"action"}:
                raise ValueError("Ожидается только поле action")
            job = start_job(payload["action"])
        except ValueError as exc:
            self.send_json(422, {"detail": str(exc)})
            return
        except RuntimeError as exc:
            self.send_json(409, {"detail": str(exc)})
            return
        except (OSError, subprocess.SubprocessError) as exc:
            self.send_json(503, {"detail": "Не удалось запустить локальную команду: " + str(exc)})
            return
        self.send_json(202, job)


def main() -> None:
    os.umask(0o077)
    read_token()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
