#!/usr/bin/env bash
# ACHEWIW — only project-scoped containers. Never manages host Ollama or Ubuntu services.
set -euo pipefail

PROJECT="achewiw"
PORT="7080"
ACTION="${1:-status}"

die() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

case "$ACTION" in
  status|stop) ;;
  *) die "Использование: bash scripts/achewiw-control.sh status|stop" ;;
esac
[[ $# -le 1 ]] || die "Допускается только одна команда: status или stop"

command -v docker >/dev/null 2>&1 || die "Docker CLI не найден; ничего не изменено"
docker info --format '{{.ServerVersion}}' >/dev/null 2>&1 ||
  die "Нет доступа к Docker daemon. Проверьте группу docker или используйте sudo только для этого скрипта."

port_status() {
  if ! command -v ss >/dev/null 2>&1; then
    echo "Порт $PORT: проверить не удалось (нет ss)"
    return
  fi
  local listeners
  if ! listeners="$(ss -H -ltn "( sport = :$PORT )" 2>/dev/null)"; then
    echo "Порт $PORT: не удалось проверить состояние"
  elif [[ -n "$listeners" ]]; then
    echo "Порт $PORT: есть слушатель. Не обязательно ACHEWIW — чужие процессы не трогаем."
    printf '%s\n' "$listeners"
  else
    echo "Порт $PORT: локального TCP-слушателя нет (приложение не должно открываться)"
  fi
}

project_containers() {
  docker ps -a --filter "label=com.docker.compose.project=$PROJECT" \
    --format '  {{.Names}} — {{.Status}}'
}

running_ids() {
  docker ps --filter "label=com.docker.compose.project=$PROJECT" \
    --format '{{.ID}}'
}

echo "ACHEWIW — действие: $ACTION; только Docker Compose project=$PROJECT"
echo "Серверная Ollama (127.0.0.1:11434), другие контейнеры, systemd, UFW: без изменений."

if [[ "$ACTION" == "status" ]]; then
  project_containers
  port_status
  exit 0
fi

# docker ps result is checked before anything is stopped.
active="$(running_ids)" || die "Не удалось получить список контейнеров ACHEWIW — остановка отменена"
if [[ -z "$active" ]]; then
  echo "Контейнеры ACHEWIW уже остановлены или не создавались."
else
  mapfile -t ids <<< "$active"
  echo "Останавливаем ТОЛЬКО контейнеры Compose-проекта '$PROJECT' (до 60 секунд на завершение):"
  docker stop --time 60 "${ids[@]}"
fi

remaining="$(running_ids)" || die "Не удалось подтвердить остановку контейнеров ACHEWIW"
if [[ -n "$remaining" ]]; then
  die "Некоторые контейнеры ACHEWIW ещё работают; проверьте docker ps"
fi

echo "Контейнеры ACHEWIW остановлены. Конфигурация, SQLite и образы сохранены."
port_status
echo "Повторный запуск этим скриптом не предусмотрен."
