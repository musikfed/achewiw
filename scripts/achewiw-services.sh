#!/usr/bin/env bash
# Single, project-scoped entry point: Studio (Docker) + local image AI (ComfyUI).
# Never touches Ollama, global Docker resources, system-wide services or foreign port owners.
set -euo pipefail
ROOT=/1tbdisk/achewiw
ACTION="${1:-status}"
die(){ printf 'ACHEWIW services: ОСТАНОВЛЕНО — %s\n' "$*" >&2; exit 1; }
msg(){ printf 'ACHEWIW services: %s\n' "$*"; }
case "$ACTION" in
  status|start|stop|start-all|studio-start|studio-stop|graphics-start|graphics-stop) ;;
  *) die "Команды: status | start | stop | start-all | studio-start | studio-stop | graphics-start | graphics-stop" ;;
esac
[[ $# -le 1 && -d "$ROOT" && ! -L "$ROOT" && -d "$ROOT/data" && ! -L "$ROOT/data" ]] ||
  die "Неверный каталог или аргументы"
[[ "$(id -u)" -ne 0 ]] || die "Запускай от обычного пользователя, не root"
for name in achewiw-control.sh comfyui-local.sh comfyui-control-agent.sh; do
  [[ -f "$ROOT/scripts/$name" && ! -L "$ROOT/scripts/$name" ]] ||
    die "Не найден ожидаемый scripts/$name"
done
cd "$ROOT"
studio(){ bash "$ROOT/scripts/achewiw-control.sh" "$1"; }
image_engine(){ bash "$ROOT/scripts/comfyui-local.sh" "$1"; }
controller(){ bash "$ROOT/scripts/comfyui-control-agent.sh" "$1"; }

# Do not stop a generating book or a running illustration job by accident.
# The SQLite connection is strictly read-only.
assert_studio_idle(){
  command -v python3 >/dev/null || die "Python3 требуется для проверки очереди"
  python3 - "$ROOT/data/achewiw.sqlite3" <<'PY'
import sqlite3, sys, time
from pathlib import Path
p=Path(sys.argv[1])
if not p.is_file() or p.is_symlink():
    raise SystemExit("ACHEWIW services: не могу безопасно проверить SQLite; остановка отменена")
try:
    with sqlite3.connect(p.as_uri()+"?mode=ro", uri=True, timeout=5) as con:
        tables={row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "ai_jobs" not in tables:
            raise RuntimeError("нет таблицы ai_jobs")
        text=con.execute("SELECT count(*) FROM ai_jobs WHERE status IN ('queued','running')").fetchone()[0]
        production=(con.execute("SELECT count(*) FROM production_jobs WHERE state IN ('queued','running')").fetchone()[0]
                    if "production_jobs" in tables else 0)
        image=(con.execute("SELECT count(*) FROM achewiw_gpu_lease WHERE expires_at>?",(time.time(),)).fetchone()[0]
               if "achewiw_gpu_lease" in tables else 0)
except (sqlite3.Error, RuntimeError) as e:
    raise SystemExit("ACHEWIW services: не могу проверить занятость: "+str(e))
print(f"ACHEWIW: активные текстовые={text}, публикации={production}, GPU-аренды={image}")
if text or production or image:
    raise SystemExit("ACHEWIW services: задачи активны; завершите их перед остановкой")
PY
}
assert_controller_idle(){
  local current
  current="$(systemctl --user is-active achewiw-comfyui-control.service 2>/dev/null || true)"
  [[ "$current" == active ]] || return 0
  controller status >/dev/null || die "Чужой или непроверенный ComfyUI controller"
  python3 - "$ROOT/data/comfyui-control.token" <<'PY'
import json,sys
from pathlib import Path
from urllib.request import Request,urlopen
p=Path(sys.argv[1])
if not p.is_file() or p.is_symlink():
    raise SystemExit("ACHEWIW: отсутствует защищённый токен controller; не останавливаем")
try:
    req=Request("http://127.0.0.1:8190/status",
                headers={"X-ACHEWIW-Control-Token":p.read_text(encoding="utf-8").strip()})
    with urlopen(req,timeout=6) as response: status=json.load(response)
except Exception as exc:
    raise SystemExit("ACHEWIW: контроллер недоступен; безопасная остановка отменена: "+str(exc))
if not isinstance(status,dict) or status.get("busy") is not False:
    raise SystemExit("ACHEWIW: controller выполняет загрузку/рендер; остановка отменена")
PY
}
image_owner_report(){
  IMAGE_REPORT="$(image_engine status 2>&1)" || die "Не удалось проверить ComfyUI"
  printf '%s\n' "$IMAGE_REPORT"
}
image_is_owned(){ [[ "$IMAGE_REPORT" == *"Наш ComfyUI работает PID="* ]]; }
assert_image_queue_idle(){
  python3 - <<'PY'
import json
from urllib.request import urlopen
try:
    with urlopen("http://127.0.0.1:8188/queue",timeout=5) as response: q=json.load(response)
except Exception as exc:
    raise SystemExit("ACHEWIW: не удалось проверить очередь ComfyUI: "+str(exc))
if not isinstance(q,dict) or q.get("queue_running") is None or q.get("queue_pending") is None:
    raise SystemExit("ACHEWIW: неизвестный формат очереди ComfyUI")
if q["queue_running"] or q["queue_pending"]:
    raise SystemExit("ACHEWIW: изображения ещё обрабатываются/в очереди; остановка отменена")
PY
}
graphics_start(){
  image_owner_report
  if ! image_is_owned; then image_engine start; fi
  controller start
  msg "графический ИИ и внутренний контроллер запущены"
}
graphics_stop(){
  image_owner_report
  local owned=no
  if image_is_owned; then
    owned=yes
    assert_image_queue_idle
  elif [[ "$IMAGE_REPORT" == *"API ComfyUI на localhost:8188 отвечает"* ||
          "$IMAGE_REPORT" == *"Порт 8188 занят"* ]]; then
    die "На 8188 отвечает неподтверждённый процесс; чужие программы не выключаем"
  fi
  assert_controller_idle
  if command -v systemctl >/dev/null && [[ "$(systemctl --user is-active achewiw-comfyui-control.service 2>/dev/null || true)" == active ]]; then
    controller stop
  fi
  if [[ "$owned" == yes ]]; then image_engine stop
  else msg "собственный ComfyUI уже выключен"; fi
  msg "графический ИИ выключен; Ollama и другие приложения не тронуты"
}
case "$ACTION" in
  status)
    studio status
    controller status
    image_engine status
    ;;
  start|studio-start) studio start ;;
  studio-stop) assert_studio_idle; studio studio-stop ;;
  graphics-start) graphics_start ;;
  graphics-stop) graphics_stop ;;
  start-all) studio start; graphics_start ;;
  stop)
    assert_studio_idle
    graphics_stop
    studio studio-stop
    msg "Studio и графический ИИ остановлены; данные и модели сохранены"
    ;;
esac
