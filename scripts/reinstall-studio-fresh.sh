#!/usr/bin/env bash
# EXPLICITLY DESTRUCTIVE: fresh ACHEWIW library, images and backups.
# Invoked only by the owner. NEVER prune Docker globally or alter host Ollama.
set -euo pipefail
ROOT=/1tbdisk/achewiw
BRANCH=feature/technical-epub-first
ACTION="${1:-preview}"
TOKEN=ERASE_ACHEWIW_LIBRARY_AND_ARCHIVES
die() { echo "ОСТАНОВЛЕНО: $*" >&2; exit 1; }
[[ "$ACTION" == preview || "$ACTION" == "$TOKEN" ]] || die "Аргумент: preview или $TOKEN"
[[ -d "$ROOT" && ! -L "$ROOT" ]] || die "Некорректная папка"
cd "$ROOT"
[[ "$(git rev-parse --show-toplevel)" == "$ROOT" ]] || die "Git в другой папке"
[[ "$(git branch --show-current)" == "$BRANCH" ]] || die "Другая ветка"
case "$(git remote get-url origin)" in
  https://github.com/musikfed/achewiw|https://github.com/musikfed/achewiw.git|git@github.com:musikfed/achewiw|git@github.com:musikfed/achewiw.git) ;;
  *) die "Чужой remote" ;;
esac
[[ -z "$(git status --porcelain)" ]] || { git status --short; die "Есть локальные изменения"; }
command -v docker >/dev/null || die "Нет Docker"
docker info >/dev/null 2>&1 || die "Нет доступа к Docker daemon"
compose() { docker compose --project-name achewiw --project-directory "$ROOT" -f "$ROOT/compose.yaml" "$@"; }
[[ "$(compose config --services)" == studio ]] || die "В Compose есть неизвестные сервисы"
ids="$(docker ps -a --no-trunc --filter label=com.docker.compose.project=achewiw --format '{{.ID}}')" || die "Нет доступа к контейнерам"
named="$(docker ps -a --no-trunc --filter 'name=^/achewiw-studio$' --format '{{.ID}}')" || die "Не удалось проверить имя"
for id in $ids $named; do
  details="$(docker inspect --format '{{.Name}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{index .Config.Labels "com.docker.compose.project.working_dir"}}|{{index .Config.Labels "org.achewiw.lifecycle"}}|{{index .Config.Labels "org.achewiw.workspace"}}|{{.HostConfig.NetworkMode}}|{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "$id")" || die "Не удалось проверить Docker $id"
  [[ "$details" == "/achewiw-studio|achewiw|studio|$ROOT|managed|$ROOT|host|$ROOT/data" ]] ||
    die "Контейнер $id не подтверждён как собственный ACHEWIW. Ничего не выключено"
done
if [[ "$ACTION" == preview ]]; then
  python3 scripts/erase-studio-library.py --dry-run
  echo "Предварительный просмотр: без остановки и без удаления."
  echo "Чтобы сделать чистую библиотеку, выполни:"
  echo "  bash scripts/reinstall-studio-fresh.sh $TOKEN"
  exit 0
fi
echo "=== Обновляем только ACHEWIW из GitHub ==="
git fetch --no-tags origin
git merge --ff-only "origin/$BRANCH"
bash -n scripts/achewiw-control.sh
bash -n scripts/monitor-v2.sh
echo "=== Останавливаем только подтверждённый studio ==="
compose stop --timeout 60 studio || die "Studio не остановлен"
[[ -z "$(docker ps --filter label=com.docker.compose.project=achewiw --format '{{.ID}}')" ]] ||
  die "Контейнер ACHEWIW всё ещё работает"
echo "=== НЕОБРАТИМО удаляем только ACHEWIW SQLite/архивы/иллюстрации ==="
python3 scripts/erase-studio-library.py --confirm "$TOKEN" ||
  die "Очистка не завершена. Studio не перезапускаем."
echo "=== Собираем заново только ACHEWIW ==="
compose up -d --build --force-recreate --no-deps studio ||
  die "Пересборка не удалась; другие проекты не тронуты"
echo "=== Проверяем отсутствие HTTP Basic ==="
success=false
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if bash scripts/achewiw-control.sh auth-check; then success=true; break; fi
  sleep 2
done
[[ "$success" == true ]] || die "Контейнер запущен, но HTTP-проверка не прошла"
echo "=== Включаем собственный хостовый монитор v2 ==="
if bash scripts/monitor-v2.sh start; then
  echo "Монитор CPU/RAM/GPU запущен."
else
  echo "ВНИМАНИЕ: монитор не запущен. bash scripts/monitor-v2.sh status" >&2
fi
echo "Новая пустая база будет создана FastAPI при первом старте контейнера."
echo "Проверено: никакие старые книги, QA, SQLite-backups или иллюстрации не перенесены."
echo "Ни другие контейнеры, ни общая Ollama, ни .env НЕ были очищены."
