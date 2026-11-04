#!/usr/bin/env bash
# ACHEWIW isolated lifecycle and GitHub fast-forward. NEVER touches Ollama or other projects.
set -euo pipefail

PROJECT="achewiw"
WORKSPACE="/1tbdisk/achewiw"
PORT=7080
# Extend this explicit allow-list only when the corresponding ACHEWIW service is implemented.
ALLOWED_SERVICES=(studio)
ACTION="${1:-status}"

die() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

case "$ACTION" in
  start|status|stop|restart|update|rebuild|auth-check|monitor-start) ;;
  *) die "Использование: bash scripts/achewiw-control.sh start|status|stop|restart|update|rebuild|auth-check|monitor-start" ;;
esac
[[ $# -le 1 ]] || die "Допускается только одна команда"
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

# Optional user-systemd telemetry is part of ACHEWIW only when the exact
# repository-owned unit is linked. Do not stop a unit on its NAME alone.
UNIT="achewiw-host-monitor.service"
unit_owned=false
if command -v systemctl >/dev/null 2>&1; then
  load_state="$(systemctl --user show "$UNIT" -p LoadState --value 2>/dev/null || true)"
  if [[ "$load_state" == loaded ]]; then
    fragment="$(systemctl --user show "$UNIT" -p FragmentPath --value 2>/dev/null || true)"
    working_dir="$(systemctl --user show "$UNIT" -p WorkingDirectory --value 2>/dev/null || true)"
    run_cmd="$(systemctl --user show "$UNIT" -p ExecStart --value 2>/dev/null || true)"
    if [[ "$fragment" != "$WORKSPACE/ops/$UNIT" ||
          "$working_dir" != "$WORKSPACE" ||
          "$run_cmd" != *"$WORKSPACE/scripts/achewiw_host_monitor.py"* ]]; then
      # For read-only 'status', show the mismatch, not a false ownership claim.
      if [[ "$ACTION" != status ]]; then
        die "Обнаружен одноимённый, но неподтверждённый user-unit. Не останавливаем НИЧЕГО."
      fi
      echo "ВНИМАНИЕ: одноимённый user-unit не принадлежит ACHEWIW; не управляем им."
    else
      unit_owned=true
    fi
  fi
fi


# Any mutating action is serialized per project; no host daemon lock or other services.
if [[ "$ACTION" != status ]]; then
  [[ -d "$WORKSPACE" && ! -L "$WORKSPACE" && -f "$WORKSPACE/compose.yaml" &&
     ! -L "$WORKSPACE/compose.yaml" ]] || die "Нет ожидаемого каталога и compose.yaml: $WORKSPACE"
  command -v flock >/dev/null 2>&1 || die "Не найден flock"
  [[ ! -L "$WORKSPACE/.achewiw-control.lock" ]] || die "Lock symlink запрещён"
  exec 9>"$WORKSPACE/.achewiw-control.lock"
  flock -n 9 || die "Другая операция ACHEWIW выполняется"
fi

compose() {
  (cd "$WORKSPACE" && docker compose --project-name "$PROJECT" \
    --project-directory "$WORKSPACE" --file "$WORKSPACE/compose.yaml" "$@")
}
prepare_compose() {
  # Passwordless by design; private LAN/VPN client-CIDR check is enforced by FastAPI.
  # Existing .env (if any) may still contain old password keys; they are IGNORED.
  if [[ -e "$WORKSPACE/.env" || -L "$WORKSPACE/.env" ]]; then
    [[ -f "$WORKSPACE/.env" && ! -L "$WORKSPACE/.env" ]] ||
      die ".env должен быть обычным файлом (не символической ссылкой)"
  fi
  [[ -d "$WORKSPACE/data" && ! -L "$WORKSPACE/data" ]] ||
    die "Создайте обычную папку data/ для книг и SQLite"
  compose config --quiet || die "Ошибка Compose/.env; ничего не запущено"
  local services
  services="$(compose config --services)" || die "Нельзя проверить Compose services"
  [[ "$services" == studio ]] || die "Разрешена только служба studio"
}

preflight_all() {
  # Also check fixed container name: do not replace an unrelated/legacy container.
  local candidates named id
  candidates="$(containers true)" || die "Не удалось получить контейнеры ACHEWIW"
  named="$(docker ps -a --no-trunc --filter "name=^/achewiw-studio$" --format '{{.ID}}')" ||
    die "Нельзя проверить занятость имени achewiw-studio"
  for id in $candidates $named; do
    verify_owned "$id" || die "Неизвестный или устаревший контейнер $id: никаких изменений"
  done
}
port_free() {
  command -v ss >/dev/null 2>&1 || die "Утилита ss обязательна перед запуском"
  local listeners
  listeners="$(ss -H -ltn "( sport = :$PORT )" 2>/dev/null)" ||
    die "Нельзя проверить порт 7080"
  [[ -z "$listeners" ]] || die "Порт 7080 занят; чужие процессы не останавливаем"
}
start_monitor() {
  if [[ "$unit_owned" == true ]]; then
    systemctl --user start "$UNIT" ||
      die "Studio работает, но собственный host-monitor не удалось включить"
  fi
}
# The CPU/RAM/NVIDIA collector is an unprivileged *ACHEWIW-owned* unit.
# Never enable an unrelated service, Docker privileged mode or host Ollama.
monitor_start() {
  local file="$WORKSPACE/ops/$UNIT" current_fragment current_workdir current_exec state
  if [[ ! -f "$file" || -L "$file" || ! -d "$WORKSPACE/data" ]]; then
    echo "Хостовый монитор: отсутствует свой unit или каталог data/" >&2
    return 1
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "Хостовый монитор: systemctl --user недоступен" >&2
    return 1
  fi
  state="$(systemctl --user show "$UNIT" -p LoadState --value 2>/dev/null)" || {
    echo "Хостовый монитор: нет доступа к systemd --user" >&2; return 1;
  }
  if [[ "$state" != loaded ]]; then
    if [[ "$state" != not-found && -n "$state" ]]; then
      echo "Хостовый монитор: неизвестный unit, не заменяем" >&2
      return 1
    fi
    systemctl --user link "$file" || return 1
    systemctl --user daemon-reload || return 1
  fi
  current_fragment="$(systemctl --user show "$UNIT" -p FragmentPath --value 2>/dev/null)" || return 1
  current_workdir="$(systemctl --user show "$UNIT" -p WorkingDirectory --value 2>/dev/null)" || return 1
  current_exec="$(systemctl --user show "$UNIT" -p ExecStart --value 2>/dev/null)" || return 1
  if [[ "$current_fragment" != "$file" || "$current_workdir" != "$WORKSPACE" ||
        "$current_exec" != *"$WORKSPACE/scripts/achewiw_host_monitor.py"* ]]; then
    echo "Хостовый монитор: одноимённая служба не принадлежит ACHEWIW. НЕ запускать." >&2
    return 1
  fi
  systemctl --user start "$UNIT" || return 1
  echo "Хостовый монитор ACHEWIW запущен: CPU / RAM / NVIDIA каждые 5 секунд"
  unit_owned=true
}
monitor_status() {
  local sample="$WORKSPACE/data/monitor/host.json" stamp seconds
  if [[ ! -f "$sample" || -L "$sample" ]]; then
    echo "Мониторинг CPU/GPU: нет файла; запусти monitor-start"
    return
  fi
  stamp="$(stat -c %Y "$sample" 2>/dev/null)" || {
    echo "Мониторинг CPU/GPU: нельзя проверить файл"; return;
  }
  seconds="$(( $(date +%s) - stamp ))"
  if (( seconds >= 0 && seconds < 30 )); then
    echo "Мониторинг CPU/GPU: свежий снимок ($seconds сек. назад)"
  else
    echo "Мониторинг CPU/GPU: устарел ($seconds сек.); проверь monitor-start"
  fi
}
run_start() {
  preflight_all
  prepare_compose
  local running
  running="$(containers false)" || die "Нельзя прочитать запущенные контейнеры"
  if [[ -z "$running" ]]; then
    port_free
    compose up -d --build --no-deps studio || die "Docker Compose studio не запустился"
  else
    echo "ACHEWIW уже запущен; не запускаем повторно"
  fi
  preflight_all
  [[ -n "$(containers false)" ]] || die "Контейнер ACHEWIW не запущен"
  if ! monitor_start; then
    echo "ВНИМАНИЕ: Studio работает, но host-monitor не запущен. Выполни monitor-start и посмотри ошибку." >&2
  fi
  echo "ACHEWIW: studio работает; данные сохранены"
  port_status
  monitor_status
}
run_restart() {
  preflight_all
  prepare_compose
  local running id
  running="$(containers false)" || die "Нельзя получить список контейнеров"
  if [[ -z "$running" ]]; then run_start; return; fi
  while IFS= read -r id; do
    [[ -z "$id" ]] && continue
    verify_owned "$id" || die "Метки поменялись; перезапуск отменён"
    docker restart --time 60 "$id" || die "Restart контейнера не выполнен"
  done <<< "$running"
  if ! monitor_start; then
    echo "ВНИМАНИЕ: Studio перезапущен, но host-monitor недоступен." >&2
  fi
  echo "Перезапущен только ACHEWIW; Ollama не затронута"
  port_status
}
# Probe the actual HTTP listener without a username/password or browser cache.
# This detects a stale image, external reverse proxy, or second HTTP service
# still challenging with WWW-Authenticate: Basic. No other services are touched.
auth_check() {
  command -v curl >/dev/null 2>&1 || die "Нужен curl для проверки входа без пароля"
  local host="192.168.0.100" url headers status
  for url in "http://127.0.0.1:$PORT/" "http://$host:$PORT/"; do
    if ! headers="$(curl --silent --show-error --max-time 8 --noproxy '*' \
        --header "Host: $host:$PORT" --dump-header - --output /dev/null "$url")"; then
      die "Нет ответа от $url. Проверь docker compose ps и ss -ltnp."
    fi
    status="$(printf '%s\n' "$headers" | awk '/^HTTP/ {code=$2} END {print code}')"
    if [[ "$status" == 401 ]] || printf '%s\n' "$headers" | grep -Eiq '^WWW-Authenticate:[[:space:]]*Basic'; then
      printf 'ACHEWIW: %s возвращает HTTP %s с запросом пароля!\n' "$url" "$status" >&2
      die "Старая сборка или другой сервер/прокси на 7080. Ничего постороннего не останавливаем."
    fi
    [[ "$status" == 200 ]] || die "HTTP $status от $url — сайт не готов"
    if ! printf '%s\n' "$headers" | grep -Fqi 'X-ACHEWIW-App: studio' ||
       ! printf '%s\n' "$headers" | grep -Fqi 'X-ACHEWIW-Auth: private-vpn-no-password'; then
      die "$url отвечает HTTP 200, но НЕ подтверждает новую ACHEWIW Studio."
    fi
    echo "ACHEWIW: $url: HTTP 200; новая Studio без HTTP Basic подтверждена."
  done
}

# Unlike restart, rebuild explicitly creates a NEW image and recreates ONLY the
# verified ACHEWIW studio container. No docker compose down, no pruning.
run_rebuild() {
  preflight_all
  prepare_compose
  local running current
  running="$(containers false)" || die "Не удаётся прочитать собственные контейнеры"
  if [[ -z "$running" ]]; then
    port_free
    die "ACHEWIW остановлен: rebuild не запускает ранее остановленный проект. Используй start отдельно."
  fi
  echo "Проверено: пересборка только собственного контейнера ACHEWIW Studio."
  compose up -d --build --force-recreate --no-deps studio ||
    die "Не удалось пересобрать ACHEWIW. Данные data/ и остальные контейнеры не тронуты."
  preflight_all
  [[ -n "$(containers false)" ]] || die "Контейнер ACHEWIW после пересборки не запущен"
  auth_check || die "Новая сборка не прошла проверку отсутствия пароля"
  current="$(git -C "$WORKSPACE" rev-parse --short HEAD 2>/dev/null || true)"
  if [[ -n "$current" ]]; then
    [[ ! -L "$WORKSPACE/data/.achewiw-deployed-commit" ]] ||
      die "Недопустимый симлинк маркера развёртывания"
    printf '%s\n' "$current" > "$WORKSPACE/data/.achewiw-deployed-commit"
  fi
  echo "ACHEWIW: новая сборка подтверждена. Никаких паролей."
}

run_update() {
  command -v git >/dev/null 2>&1 || die "Git не установлен"
  [[ -d "$WORKSPACE/.git" && ! -L "$WORKSPACE/.git" ]] || die "Не найден Git checkout"
  preflight_all
  local origin branch dirty running before after
  origin="$(git -C "$WORKSPACE" remote get-url origin)" || die "Нет origin"
  case "$origin" in
    https://github.com/musikfed/achewiw|https://github.com/musikfed/achewiw.git|git@github.com:musikfed/achewiw|git@github.com:musikfed/achewiw.git) ;;
    *) die "Чужой Git origin: $origin. Обновление отменено" ;;
  esac
  branch="$(git -C "$WORKSPACE" symbolic-ref --quiet --short HEAD)" ||
    die "Detached HEAD; ветку автоматически не переключаем"
  [[ "$branch" == feature/technical-epub-first ]] ||
    die "Текущая ветка $branch; рабочая Compose-версия: feature/technical-epub-first. Автопереключения нет"
  dirty="$(git -C "$WORKSPACE" status --porcelain --untracked-files=normal)" ||
    die "Нельзя проверить Git status"
  [[ -z "$dirty" ]] || die "Есть незакоммиченные/неотслеживаемые файлы; никакого reset/clean"
  running="$(containers false)" || die "Нельзя проверить состояние контейнеров"
  if [[ -n "$running" ]]; then prepare_compose; fi
  before="$(git -C "$WORKSPACE" rev-parse --short HEAD)"
  git -C "$WORKSPACE" fetch --no-tags origin \
    "+refs/heads/$branch:refs/remotes/origin/$branch" || die "Git fetch не выполнен"
  git -C "$WORKSPACE" merge-base --is-ancestor HEAD "refs/remotes/origin/$branch" ||
    die "Ветки разошлись; fast-forward невозможен, файлы не сбрасываем"
  git -C "$WORKSPACE" merge --ff-only "refs/remotes/origin/$branch" ||
    die "Git fast-forward не выполнен"
  after="$(git -C "$WORKSPACE" rev-parse --short HEAD)"
  local deployed_marker="$WORKSPACE/data/.achewiw-deployed-commit" deployed=""
  [[ ! -L "$deployed_marker" ]] || die "Маркер развёртывания не может быть ссылкой"
  if [[ -f "$deployed_marker" ]]; then
    deployed="$(cat "$deployed_marker")" || die "Нельзя прочитать локальный маркер сборки"
  fi
  echo "ACHEWIW: Git $before -> $after; .env, data/, Ollama не изменены"
  if [[ -n "$running" && ( "$before" != "$after" || "$deployed" != "$after" ) ]]; then
    preflight_all
    prepare_compose
    compose up -d --build --force-recreate --no-deps studio ||
      die "Git обновлён, но пересборка studio не удалась. Старые данные не удалены"
    preflight_all
    [[ -n "$(containers false)" ]] || die "После обновления studio не запущен"
    auth_check || die "Studio не прошёл HTTP-проверку: возможно, порт 7080 обслуживает прежний сервер"
    printf '%s\n' "$after" > "$deployed_marker"
    echo "Обновлён только работающий ACHEWIW Studio"
  elif [[ -z "$running" ]]; then
    echo "ACHEWIW был остановлен и остался остановленным; для запуска: start"
  else
    echo "Git уже актуален, контейнер не перезапускался"
  fi
}

case "$ACTION" in
  start) run_start; exit 0 ;;
  restart) run_restart; exit 0 ;;
  update) run_update; exit 0 ;;
  rebuild) run_rebuild; exit 0 ;;
  auth-check) auth_check; exit 0 ;;
  monitor-start) monitor_start || die "Не удалось включить собственный монитор; другие службы не изменены"; exit 0 ;;
esac

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
  monitor_status
  exit 0
fi

ids="$(containers false)" || die "Не удалось получить список контейнеров — выключение отменено"
if [[ -z "$ids" ]]; then
  echo "Управляемые Docker-контейнеры ACHEWIW уже остановлены или ещё не созданы."
else
  # Fail closed for the ENTIRE operation if any candidate is not in the explicit allow-list.
  owned=()
  while IFS= read -r id; do
    [[ -z "$id" ]] && continue
    verify_owned "$id" || die "Контейнер $id не имеет ВСЕХ разрешающих меток ACHEWIW. НИЧЕГО не останавливаем."
    owned+=("$id")
  done <<< "$ids"

  printf 'Проверено %d контейнер(ов) ACHEWIW. Остановка только их.\n' "${#owned[@]}"
  for id in "${owned[@]}"; do
    verify_owned "$id" || die "Метки контейнера $id изменились. Остановка прервана."
    docker stop --time 60 "$id"
  done
fi

remaining="$(containers false)" || die "Не удалось проверить результат выключения"
[[ -z "$remaining" ]] || die "Есть запущенные контейнеры с проектным именем ACHEWIW. Проверьте вручную."
if [[ "$unit_owned" == true ]]; then
  # This unit is explicitly linked from /1tbdisk/achewiw/ops.
  systemctl --user stop "$UNIT" || die "ACHEWIW Docker остановлен, но собственный монитор не удалось остановить."
  echo "Остановлен собственный хостовый монитор ACHEWIW."
fi
echo "Управляемые контейнеры ACHEWIW остановлены; база, образы и файлы не удалены."
port_status
echo "Запуска и управления другими службами этот скрипт не выполняет."
 ||
       ! printf '%s\n' "$headers" | grep -Eiq '^X-ACHEWIW-Auth:[[:space:]]*private-vpn-no-password[[:space:]]*
  done
}

# Unlike restart, rebuild explicitly creates a NEW image and recreates ONLY the
# verified ACHEWIW studio container. No docker compose down, no pruning.
run_rebuild() {
  preflight_all
  prepare_compose
  local running current
  running="$(containers false)" || die "Не удаётся прочитать собственные контейнеры"
  if [[ -z "$running" ]]; then
    port_free
    die "ACHEWIW остановлен: rebuild не запускает ранее остановленный проект. Используй start отдельно."
  fi
  echo "Проверено: пересборка только собственного контейнера ACHEWIW Studio."
  compose up -d --build --force-recreate --no-deps studio ||
    die "Не удалось пересобрать ACHEWIW. Данные data/ и остальные контейнеры не тронуты."
  preflight_all
  [[ -n "$(containers false)" ]] || die "Контейнер ACHEWIW после пересборки не запущен"
  auth_check || die "Новая сборка не прошла проверку отсутствия пароля"
  current="$(git -C "$WORKSPACE" rev-parse --short HEAD 2>/dev/null || true)"
  if [[ -n "$current" ]]; then
    [[ ! -L "$WORKSPACE/data/.achewiw-deployed-commit" ]] ||
      die "Недопустимый симлинк маркера развёртывания"
    printf '%s\n' "$current" > "$WORKSPACE/data/.achewiw-deployed-commit"
  fi
  echo "ACHEWIW: новая сборка подтверждена. Никаких паролей."
}

run_update() {
  command -v git >/dev/null 2>&1 || die "Git не установлен"
  [[ -d "$WORKSPACE/.git" && ! -L "$WORKSPACE/.git" ]] || die "Не найден Git checkout"
  preflight_all
  local origin branch dirty running before after
  origin="$(git -C "$WORKSPACE" remote get-url origin)" || die "Нет origin"
  case "$origin" in
    https://github.com/musikfed/achewiw|https://github.com/musikfed/achewiw.git|git@github.com:musikfed/achewiw|git@github.com:musikfed/achewiw.git) ;;
    *) die "Чужой Git origin: $origin. Обновление отменено" ;;
  esac
  branch="$(git -C "$WORKSPACE" symbolic-ref --quiet --short HEAD)" ||
    die "Detached HEAD; ветку автоматически не переключаем"
  [[ "$branch" == feature/technical-epub-first ]] ||
    die "Текущая ветка $branch; рабочая Compose-версия: feature/technical-epub-first. Автопереключения нет"
  dirty="$(git -C "$WORKSPACE" status --porcelain --untracked-files=normal)" ||
    die "Нельзя проверить Git status"
  [[ -z "$dirty" ]] || die "Есть незакоммиченные/неотслеживаемые файлы; никакого reset/clean"
  running="$(containers false)" || die "Нельзя проверить состояние контейнеров"
  if [[ -n "$running" ]]; then prepare_compose; fi
  before="$(git -C "$WORKSPACE" rev-parse --short HEAD)"
  git -C "$WORKSPACE" fetch --no-tags origin \
    "+refs/heads/$branch:refs/remotes/origin/$branch" || die "Git fetch не выполнен"
  git -C "$WORKSPACE" merge-base --is-ancestor HEAD "refs/remotes/origin/$branch" ||
    die "Ветки разошлись; fast-forward невозможен, файлы не сбрасываем"
  git -C "$WORKSPACE" merge --ff-only "refs/remotes/origin/$branch" ||
    die "Git fast-forward не выполнен"
  after="$(git -C "$WORKSPACE" rev-parse --short HEAD)"
  local deployed_marker="$WORKSPACE/data/.achewiw-deployed-commit" deployed=""
  [[ ! -L "$deployed_marker" ]] || die "Маркер развёртывания не может быть ссылкой"
  if [[ -f "$deployed_marker" ]]; then
    deployed="$(cat "$deployed_marker")" || die "Нельзя прочитать локальный маркер сборки"
  fi
  echo "ACHEWIW: Git $before -> $after; .env, data/, Ollama не изменены"
  if [[ -n "$running" && ( "$before" != "$after" || "$deployed" != "$after" ) ]]; then
    preflight_all
    prepare_compose
    compose up -d --build --force-recreate --no-deps studio ||
      die "Git обновлён, но пересборка studio не удалась. Старые данные не удалены"
    preflight_all
    [[ -n "$(containers false)" ]] || die "После обновления studio не запущен"
    auth_check || die "Studio не прошёл HTTP-проверку: возможно, порт 7080 обслуживает прежний сервер"
    printf '%s\n' "$after" > "$deployed_marker"
    echo "Обновлён только работающий ACHEWIW Studio"
  elif [[ -z "$running" ]]; then
    echo "ACHEWIW был остановлен и остался остановленным; для запуска: start"
  else
    echo "Git уже актуален, контейнер не перезапускался"
  fi
}

case "$ACTION" in
  start) run_start; exit 0 ;;
  restart) run_restart; exit 0 ;;
  update) run_update; exit 0 ;;
  rebuild) run_rebuild; exit 0 ;;
  auth-check) auth_check; exit 0 ;;
  monitor-start) monitor_start || die "Не удалось включить собственный монитор; другие службы не изменены"; exit 0 ;;
esac

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
  monitor_status
  exit 0
fi

ids="$(containers false)" || die "Не удалось получить список контейнеров — выключение отменено"
if [[ -z "$ids" ]]; then
  echo "Управляемые Docker-контейнеры ACHEWIW уже остановлены или ещё не созданы."
else
  # Fail closed for the ENTIRE operation if any candidate is not in the explicit allow-list.
  owned=()
  while IFS= read -r id; do
    [[ -z "$id" ]] && continue
    verify_owned "$id" || die "Контейнер $id не имеет ВСЕХ разрешающих меток ACHEWIW. НИЧЕГО не останавливаем."
    owned+=("$id")
  done <<< "$ids"

  printf 'Проверено %d контейнер(ов) ACHEWIW. Остановка только их.\n' "${#owned[@]}"
  for id in "${owned[@]}"; do
    verify_owned "$id" || die "Метки контейнера $id изменились. Остановка прервана."
    docker stop --time 60 "$id"
  done
fi

remaining="$(containers false)" || die "Не удалось проверить результат выключения"
[[ -z "$remaining" ]] || die "Есть запущенные контейнеры с проектным именем ACHEWIW. Проверьте вручную."
if [[ "$unit_owned" == true ]]; then
  # This unit is explicitly linked from /1tbdisk/achewiw/ops.
  systemctl --user stop "$UNIT" || die "ACHEWIW Docker остановлен, но собственный монитор не удалось остановить."
  echo "Остановлен собственный хостовый монитор ACHEWIW."
fi
echo "Управляемые контейнеры ACHEWIW остановлены; база, образы и файлы не удалены."
port_status
echo "Запуска и управления другими службами этот скрипт не выполняет."
; then
      die "$label отвечает HTTP 200, но НЕ подтверждает новую ACHEWIW Studio. Возможен чужой сервер, старый контейнер или прокси."
    fi
    echo "ACHEWIW: $label отвечает HTTP 200; подтверждена новая Studio без HTTP Basic."
  done
}

# Unlike restart, rebuild explicitly creates a NEW image and recreates ONLY the
# verified ACHEWIW studio container. No docker compose down, no pruning.
run_rebuild() {
  preflight_all
  prepare_compose
  local running current
  running="$(containers false)" || die "Не удаётся прочитать собственные контейнеры"
  if [[ -z "$running" ]]; then
    port_free
    die "ACHEWIW остановлен: rebuild не запускает ранее остановленный проект. Используй start отдельно."
  fi
  echo "Проверено: пересборка только собственного контейнера ACHEWIW Studio."
  compose up -d --build --force-recreate --no-deps studio ||
    die "Не удалось пересобрать ACHEWIW. Данные data/ и остальные контейнеры не тронуты."
  preflight_all
  [[ -n "$(containers false)" ]] || die "Контейнер ACHEWIW после пересборки не запущен"
  auth_check || die "Новая сборка не прошла проверку отсутствия пароля"
  current="$(git -C "$WORKSPACE" rev-parse --short HEAD 2>/dev/null || true)"
  if [[ -n "$current" ]]; then
    [[ ! -L "$WORKSPACE/data/.achewiw-deployed-commit" ]] ||
      die "Недопустимый симлинк маркера развёртывания"
    printf '%s\n' "$current" > "$WORKSPACE/data/.achewiw-deployed-commit"
  fi
  echo "ACHEWIW: новая сборка подтверждена. Никаких паролей."
}

run_update() {
  command -v git >/dev/null 2>&1 || die "Git не установлен"
  [[ -d "$WORKSPACE/.git" && ! -L "$WORKSPACE/.git" ]] || die "Не найден Git checkout"
  preflight_all
  local origin branch dirty running before after
  origin="$(git -C "$WORKSPACE" remote get-url origin)" || die "Нет origin"
  case "$origin" in
    https://github.com/musikfed/achewiw|https://github.com/musikfed/achewiw.git|git@github.com:musikfed/achewiw|git@github.com:musikfed/achewiw.git) ;;
    *) die "Чужой Git origin: $origin. Обновление отменено" ;;
  esac
  branch="$(git -C "$WORKSPACE" symbolic-ref --quiet --short HEAD)" ||
    die "Detached HEAD; ветку автоматически не переключаем"
  [[ "$branch" == feature/technical-epub-first ]] ||
    die "Текущая ветка $branch; рабочая Compose-версия: feature/technical-epub-first. Автопереключения нет"
  dirty="$(git -C "$WORKSPACE" status --porcelain --untracked-files=normal)" ||
    die "Нельзя проверить Git status"
  [[ -z "$dirty" ]] || die "Есть незакоммиченные/неотслеживаемые файлы; никакого reset/clean"
  running="$(containers false)" || die "Нельзя проверить состояние контейнеров"
  if [[ -n "$running" ]]; then prepare_compose; fi
  before="$(git -C "$WORKSPACE" rev-parse --short HEAD)"
  git -C "$WORKSPACE" fetch --no-tags origin \
    "+refs/heads/$branch:refs/remotes/origin/$branch" || die "Git fetch не выполнен"
  git -C "$WORKSPACE" merge-base --is-ancestor HEAD "refs/remotes/origin/$branch" ||
    die "Ветки разошлись; fast-forward невозможен, файлы не сбрасываем"
  git -C "$WORKSPACE" merge --ff-only "refs/remotes/origin/$branch" ||
    die "Git fast-forward не выполнен"
  after="$(git -C "$WORKSPACE" rev-parse --short HEAD)"
  local deployed_marker="$WORKSPACE/data/.achewiw-deployed-commit" deployed=""
  [[ ! -L "$deployed_marker" ]] || die "Маркер развёртывания не может быть ссылкой"
  if [[ -f "$deployed_marker" ]]; then
    deployed="$(cat "$deployed_marker")" || die "Нельзя прочитать локальный маркер сборки"
  fi
  echo "ACHEWIW: Git $before -> $after; .env, data/, Ollama не изменены"
  if [[ -n "$running" && ( "$before" != "$after" || "$deployed" != "$after" ) ]]; then
    preflight_all
    prepare_compose
    compose up -d --build --force-recreate --no-deps studio ||
      die "Git обновлён, но пересборка studio не удалась. Старые данные не удалены"
    preflight_all
    [[ -n "$(containers false)" ]] || die "После обновления studio не запущен"
    auth_check || die "Studio не прошёл HTTP-проверку: возможно, порт 7080 обслуживает прежний сервер"
    printf '%s\n' "$after" > "$deployed_marker"
    echo "Обновлён только работающий ACHEWIW Studio"
  elif [[ -z "$running" ]]; then
    echo "ACHEWIW был остановлен и остался остановленным; для запуска: start"
  else
    echo "Git уже актуален, контейнер не перезапускался"
  fi
}

case "$ACTION" in
  start) run_start; exit 0 ;;
  restart) run_restart; exit 0 ;;
  update) run_update; exit 0 ;;
  rebuild) run_rebuild; exit 0 ;;
  auth-check) auth_check; exit 0 ;;
  monitor-start) monitor_start || die "Не удалось включить собственный монитор; другие службы не изменены"; exit 0 ;;
esac

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
  monitor_status
  exit 0
fi

ids="$(containers false)" || die "Не удалось получить список контейнеров — выключение отменено"
if [[ -z "$ids" ]]; then
  echo "Управляемые Docker-контейнеры ACHEWIW уже остановлены или ещё не созданы."
else
  # Fail closed for the ENTIRE operation if any candidate is not in the explicit allow-list.
  owned=()
  while IFS= read -r id; do
    [[ -z "$id" ]] && continue
    verify_owned "$id" || die "Контейнер $id не имеет ВСЕХ разрешающих меток ACHEWIW. НИЧЕГО не останавливаем."
    owned+=("$id")
  done <<< "$ids"

  printf 'Проверено %d контейнер(ов) ACHEWIW. Остановка только их.\n' "${#owned[@]}"
  for id in "${owned[@]}"; do
    verify_owned "$id" || die "Метки контейнера $id изменились. Остановка прервана."
    docker stop --time 60 "$id"
  done
fi

remaining="$(containers false)" || die "Не удалось проверить результат выключения"
[[ -z "$remaining" ]] || die "Есть запущенные контейнеры с проектным именем ACHEWIW. Проверьте вручную."
if [[ "$unit_owned" == true ]]; then
  # This unit is explicitly linked from /1tbdisk/achewiw/ops.
  systemctl --user stop "$UNIT" || die "ACHEWIW Docker остановлен, но собственный монитор не удалось остановить."
  echo "Остановлен собственный хостовый монитор ACHEWIW."
fi
echo "Управляемые контейнеры ACHEWIW остановлены; база, образы и файлы не удалены."
port_status
echo "Запуска и управления другими службами этот скрипт не выполняет."
