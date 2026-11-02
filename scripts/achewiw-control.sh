#!/usr/bin/env bash
# ACHEWIW project-only shutdown. No start, no docker compose down, no host service control.
set -euo pipefail

PROJECT="achewiw"
WORKSPACE="/1tbdisk/achewiw"
PORT=7080
# Extend this explicit allow-list only when the corresponding ACHEWIW service is implemented.
ALLOWED_SERVICES=(studio)
ACTION="${1:-status}"

die() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

case "$ACTION" in
  status|stop) ;;
  *) die "Использование: bash scripts/achewiw-control.sh status|stop" ;;
esac
[[ $# -le 1 ]] || die "Допускается только одна команда: status или stop"
command -v docker >/dev/null 2>&1 || die "Docker CLI не найден"
docker info --format '{{.ServerVersion}}' >/dev/null 2>&1 ||
  die "Нет доступа к Docker daemon; ничего не изменено"

port_status() {
  if ! command -v ss >/dev/null 2>&1; then
    echo "Порт $PORT: невозможно проверить, утилита ss отсутствует"
    return
  fi
  local listeners
  if ! listeners="$(ss -H -ltn "( sport = :$PORT )" 2>/dev/null)"; then
    echo "Порт $PORT: ошибка проверки"
  elif [[ -n "$listeners" ]]; then
    echo "Порт $PORT: занят. Чужой процесс не трогаем."
    printf '%s\n' "$listeners"
  else
    echo "Порт $PORT: TCP-слушателя нет"
  fi
}

# Docker CLI gets this full immutable container ID, not an arbitrary name or user path.
containers() {
  local all="${1:-false}"
  if [[ "$all" == true ]]; then
    docker ps -a --no-trunc --filter "label=com.docker.compose.project=$PROJECT" --format '{{.ID}}'
  else
    docker ps --no-trunc --filter "label=com.docker.compose.project=$PROJECT" --format '{{.ID}}'
  fi
}

# An exact conjunction of independently defined compose identity, workspace, and
# an ACHEWIW-specific opt-in marker. A shared project name ALONE cannot pass.
verify_owned() {
  local id="$1" labels project directory service managed scope allowed=false
  labels="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.project.working_dir" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{ index .Config.Labels "org.achewiw.lifecycle" }}|{{ index .Config.Labels "org.achewiw.workspace" }}' "$id")" || return 1
  IFS='|' read -r project directory service managed scope <<< "$labels"
  [[ "$project" == "$PROJECT" && "$directory" == "$WORKSPACE" &&
     "$managed" == "managed" && "$scope" == "$WORKSPACE" ]] || return 1
  for item in "${ALLOWED_SERVICES[@]}"; do
    if [[ "$item" == "$service" ]]; then allowed=true; break; fi
  done
  [[ "$allowed" == true ]]
}

printf 'ACHEWIW: %s (только %s; Ollama и другие проекты не управляются)\n' "$ACTION" "$WORKSPACE"
if [[ "$ACTION" == status ]]; then
  ids="$(containers true)" || die "Не удалось прочитать Docker containers"
  if [[ -z "$ids" ]]; then
    echo "Контейнеров с Compose-проектом ACHEWIW не найдено."
  else
    while IFS= read -r id; do
      [[ -z "$id" ]] && continue
      if verify_owned "$id"; then
        echo "Подтверждённый ACHEWIW-контейнер: $id"
      else
        echo "ВНИМАНИЕ: контейнер с похожим Compose-именем НЕ подтверждён: $id (не останавливать)"
      fi
    done <<< "$ids"
  fi
  port_status
  exit 0
fi

ids="$(containers false)" || die "Не удалось получить список контейнеров — выключение отменено"
if [[ -z "$ids" ]]; then
  echo "Управляемые контейнеры ACHEWIW уже остановлены или ещё не созданы."
  port_status
  exit 0
fi

# Fail closed for the ENTIRE operation if any candidate is not in the explicit allow-list.
owned=()
while IFS= read -r id; do
  [[ -z "$id" ]] && continue
  verify_owned "$id" || die "Контейнер $id не имеет ВСЕХ разрешающих меток ACHEWIW. НИЧЕГО не останавливаем."
  owned+=("$id")
done <<< "$ids"

printf 'Проверено %d контейнер(ов) ACHEWIW. Остановка только их.\n' "${#owned[@]}"
for id in "${owned[@]}"; do
  # Verify again immediately before acting, in case the container was replaced.
  verify_owned "$id" || die "Метки контейнера $id изменились. Остановка прервана."
  docker stop --time 60 "$id"
done

remaining="$(containers false)" || die "Не удалось проверить результат выключения"
[[ -z "$remaining" ]] || die "Есть запущенные контейнеры с проектным именем ACHEWIW. Проверьте вручную."
echo "Управляемые контейнеры ACHEWIW остановлены; база, образы и файлы не удалены."
port_status
echo "Запуска и управления другими службами этот скрипт не выполняет."
