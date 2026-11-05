#!/usr/bin/env bash
# Install and manage only ACHEWIW's loopback ComfyUI control user-unit.
set -euo pipefail
ROOT=/1tbdisk/achewiw
UNIT=achewiw-comfyui-control.service
FILE="$ROOT/ops/$UNIT"
TOKEN="$ROOT/data/comfyui-control.token"
ACTION="${1:-status}"
die(){ echo "ACHEWIW ComfyUI control: ОСТАНОВЛЕНО — $*" >&2; exit 1; }
msg(){ echo "ACHEWIW ComfyUI control: $*"; }
[[ $# -le 1 && ( "$ACTION" == start || "$ACTION" == reload || "$ACTION" == status || "$ACTION" == stop ) ]] ||
  die "Команды: start | reload | status | stop"
[[ "$(id -u)" -ne 0 ]] || die "Используй обычного пользователя Ubuntu, не root"
[[ -d "$ROOT" && ! -L "$ROOT" && -d "$ROOT/data" && ! -L "$ROOT/data" ]] ||
  die "Недопустимый корень ACHEWIW или data/"
[[ -f "$FILE" && ! -L "$FILE" ]] || die "Нет собственного systemd unit"
[[ -f "$ROOT/scripts/comfyui_control_agent.py" && ! -L "$ROOT/scripts/comfyui_control_agent.py" ]] ||
  die "Нет собственного host-control агента"
command -v systemctl >/dev/null || die "systemctl отсутствует"
show(){ systemctl --user show "$UNIT" -p "$1" --value 2>/dev/null || true; }
fragment_target(){
  local fragment
  fragment="$(show FragmentPath)"
  [[ -n "$fragment" && -f "$fragment" ]] || return 1
  readlink -e -- "$fragment"
}
owned(){
  local resolved
  resolved="$(fragment_target)" || return 1
  [[ "$(show LoadState)" == loaded &&
     "$resolved" == "$FILE" &&
     "$(show WorkingDirectory)" == "$ROOT" &&
     "$(show ExecStart)" == *"$ROOT/scripts/comfyui_control_agent.py"* ]]
}
case "$ACTION" in
  status)
    if owned; then
      msg "unit подтверждён; ActiveState=$(show ActiveState)"
    elif [[ "$(show LoadState)" == loaded ]]; then
      die "Обнаружен одноимённый, но неподтверждённый unit; не трогаем"
    else
      msg "host-control не установлен; для установки выполни start"
    fi
    ;;
  start|reload)
    [[ -f "$ROOT/scripts/comfyui-local.sh" && ! -L "$ROOT/scripts/comfyui-local.sh" ]] ||
      die "Нет собственного scripts/comfyui-local.sh"
    [[ ! -L "$ROOT/data/comfyui-runtime" && ! -L "$ROOT/data/comfyui-control-jobs" ]] ||
      die "Каталоги runtime не должны быть символическими ссылками"
    mkdir -p "$ROOT/data/comfyui-runtime" "$ROOT/data/comfyui-control-jobs"
    [[ ! -L "$TOKEN" ]] || die "Файл токена не должен быть символической ссылкой"
    if [[ -e "$TOKEN" && ! -f "$TOKEN" ]]; then die "Токен не является обычным файлом"; fi
    if [[ ! -f "$TOKEN" ]]; then
      umask 077
      python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > "$TOKEN"
    fi
    [[ -s "$TOKEN" ]] || die "Не удалось создать токен"
    chmod 600 "$TOKEN"
    if [[ "$(show LoadState)" == loaded ]] && ! owned; then
      die "Обнаружен чужой или неподтверждённый unit; ничего не заменяем"
    fi
    if [[ "$(show LoadState)" != loaded ]]; then
      systemctl --user link "$FILE" || die "Не удалось зарегистрировать собственный unit"
      systemctl --user daemon-reload || die "Не удалось перечитать systemd user units"
    fi
    owned || die "Проверка источника unit, WorkingDirectory или ExecStart не прошла"
    if [[ "$ACTION" == reload && "$(show ActiveState)" == active ]]; then
      # Do not drop the running job registry during a large SDXL download or install.
      # If the old controller is unreachable, fail closed and leave it running.
      python3 - "$TOKEN" <<'PY' || die "Контроллер занят или недоступен: не прерываем установку/загрузку модели"
import json, sys
from pathlib import Path
from urllib.request import Request, urlopen
token = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
request = Request("http://127.0.0.1:8190/status",
                  headers={"X-ACHEWIW-Control-Token": token})
with urlopen(request, timeout=12) as response:
    state = json.load(response)
if not isinstance(state, dict) or state.get("busy") is not False:
    raise SystemExit("Текущая операция ComfyUI ещё выполняется")
PY
    fi
    systemctl --user daemon-reload || die "Не удалось перечитать собственный systemd unit"
    if [[ "$ACTION" == reload && "$(show ActiveState)" == active ]]; then
      systemctl --user restart "$UNIT" || die "Не удалось обновить собственный host-control"
    else
      systemctl --user start "$UNIT" || die "Не удалось запустить host-control"
    fi
    systemctl --user is-active --quiet "$UNIT" || die "Host-control не активен"
    msg "кнопочное управление готово: 127.0.0.1:8190; токен хранится только в data/"
    ;;
  stop)
    owned || die "Собственный host-control unit не подтверждён; никого не останавливаем"
    systemctl --user stop "$UNIT" || die "Не удалось остановить host-control"
    msg "host-control остановлен; сам ComfyUI этим действием не выключается"
    ;;
esac
