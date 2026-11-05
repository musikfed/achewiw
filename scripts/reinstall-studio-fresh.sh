#!/usr/bin/env bash
# EXPLICITLY DESTRUCTIVE: fresh ACHEWIW library, images and backups.
# Invoked only by the owner. NEVER prune Docker globally or alter host Ollama.
set -euo pipefail
ROOT=/1tbdisk/achewiw
BRANCH=feature/technical-epub-first
ACTION="${1:-preview}"
TOKEN=ERASE_ACHEWIW_LIBRARY_AND_ARCHIVES
die() { echo "ОСТАНОВЛЕНО: $*" >&2; exit 1; }
[[ $# -le 1 && ( "$ACTION" == preview || "$ACTION" == "$TOKEN" ) ]] || die "Аргумент: preview или $TOKEN"
[[ "$(id -u)" -ne 0 ]] || die "Запускай от обычного владельца ACHEWIW, без sudo"
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
# Read-only DB check: no deletion while story generation or GPU work is active.
python3 - "$ROOT/data/achewiw.sqlite3" <<'PY'
import sqlite3,sys,time
from pathlib import Path
p=Path(sys.argv[1])
if p.exists():
    if p.is_symlink() or not p.is_file(): raise SystemExit("Некорректный SQLite path")
    with sqlite3.connect(p.as_uri()+"?mode=ro",uri=True,timeout=6) as conn:
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        jobs=conn.execute("SELECT count(*) FROM ai_jobs WHERE status IN ('queued','running')").fetchone()[0] if "ai_jobs" in tables else 0
        leases=conn.execute("SELECT count(*) FROM achewiw_gpu_lease WHERE expires_at>?",(time.time(),)).fetchone()[0] if "achewiw_gpu_lease" in tables else 0
    print(f"ACHEWIW jobs={jobs}, GPU leases={leases}")
    if jobs or leases: raise SystemExit("Задачи ещё выполняются: стирание отменено")
else: print("Основная SQLite уже отсутствует")
PY
if [[ "$ACTION" == preview ]]; then
  python3 scripts/erase-studio-library.py --dry-run
  echo "Предварительный просмотр: без остановки и без удаления."
  echo "Чтобы сделать чистую библиотеку, выполни:"
  echo "  bash scripts/reinstall-studio-fresh.sh $TOKEN"
  exit 0
fi
echo "=== Обновляем только ACHEWIW из GitHub ==="
script_before="$(git hash-object scripts/reinstall-studio-fresh.sh)"
git fetch --no-tags origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" ||
  die "Не удалось получить рабочую ветку; книги не тронуты"
git merge --ff-only "origin/$BRANCH" ||
  die "Ветки разошлись; никакого Git reset и очистки данных"
script_after="$(git hash-object scripts/reinstall-studio-fresh.sh)"
if [[ "$script_before" != "$script_after" ]]; then
  echo "Обновился сам сценарий очистки: запускаем новую версию и перепроверяем условия"
  exec bash "$ROOT/scripts/reinstall-studio-fresh.sh" "$ACTION"
fi
bash -n scripts/achewiw-control.sh
bash -n scripts/monitor-v2.sh
echo "=== Останавливаем только подтверждённый studio ==="
compose stop --timeout 60 studio || die "Studio не остановлен"
[[ -z "$(docker ps --filter label=com.docker.compose.project=achewiw --format '{{.ID}}')" ]] ||
  die "Контейнер ACHEWIW всё ещё работает"
echo "=== НЕОБРАТИМО удаляем только ACHEWIW SQLite/архивы/иллюстрации ==="
python3 scripts/erase-studio-library.py --confirm "$TOKEN" ||
  die "Очистка не завершена. Studio не перезапускаем."
echo "=== Чистим только Python/test-кеши ACHEWIW ==="
find -P "$ROOT/app" "$ROOT/tests" "$ROOT/scripts" -type f -name '*.pyc' -delete
find -P "$ROOT/app" "$ROOT/tests" "$ROOT/scripts" -depth -type d \
  \( -name '__pycache__' -o -name '.pytest_cache' \) -exec rm -rf -- '{}' +
if [[ -d "$ROOT/.pytest_cache" && ! -L "$ROOT/.pytest_cache" ]]; then
  rm -rf -- "$ROOT/.pytest_cache"
fi
echo "=== Создаём маркер сброса только браузерного хранилища ACHEWIW ==="
[[ ! -L "$ROOT/data/.achewiw-library-epoch" ]] || die "Маркер сброса является симлинком"
python3 - "$ROOT/data/.achewiw-library-epoch" <<'PY'
import secrets,sys
from pathlib import Path
p=Path(sys.argv[1])
p.write_text(secrets.token_hex(20)+"\n",encoding="ascii")
p.chmod(0o644)  # browser epoch is public, not an authorization secret
PY
echo "=== Собираем ACHEWIW без старого Docker build cache (без system prune) ==="
compose build --no-cache studio || die "Чистая сборка не удалась; Studio оставлен остановленным"
compose up -d --no-build --force-recreate --no-deps studio ||
  die "Пересборка не удалась; другие проекты не тронуты"
echo "=== Проверяем отсутствие HTTP Basic ==="
success=false
for attempt in $(seq 1 30); do
  if bash scripts/achewiw-control.sh auth-check; then success=true; break; fi
  sleep 2
done
[[ "$success" == true ]] || die "Контейнер запущен, но HTTP-проверка не прошла"
echo "=== Проверяем, что новая библиотека и индекс действительно пусты ==="
python3 - "$ROOT/data" <<'PY'
from pathlib import Path
import sqlite3,sys
base=Path(sys.argv[1])
for name in ("achewiw.sqlite3","search.sqlite3","work.sqlite3"):
    p=base/name
    if p.is_symlink() or not p.is_file():
        raise SystemExit("Не создана новая база "+name)
    with sqlite3.connect(p.as_uri()+"?mode=ro",uri=True,timeout=5) as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0]=="ok",name
        for table in (("projects","ai_jobs","illustrations") if name=="achewiw.sqlite3"
                      else ("search_manifest",) if name=="search.sqlite3"
                      else ("work_proposals",)):
            count=db.execute("SELECT count(*) FROM "+table).fetchone()[0]
            if count: raise SystemExit(f"Неожиданно сохранены старые данные {name}:{table}={count}")
print("Три SQLite чистые; старых рукописей, заданий, иллюстраций и индекса нет")
PY
echo "=== Включаем собственный хостовый монитор v2 ==="
if bash scripts/monitor-v2.sh start; then
  echo "Монитор CPU/RAM/GPU запущен."
else
  echo "ВНИМАНИЕ: монитор не запущен. bash scripts/monitor-v2.sh status" >&2
fi
echo "Новая пустая база будет создана FastAPI при первом старте контейнера."
echo "Проверено: никакие старые книги, QA, SQLite-backups или иллюстрации не перенесены."
echo "Ни другие контейнеры, ни общая Ollama, SDXL/ComfyUI, ни .env НЕ были очищены."
echo "Браузер при следующем открытии очистит только собственные ключи ACHEWIW; Ctrl+Shift+R."
