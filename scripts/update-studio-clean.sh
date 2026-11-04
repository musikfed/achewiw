#!/usr/bin/env bash
# ACHEWIW-only fast-forward + no-cache Studio build, no database/model deletion.
# Usage: bash scripts/update-studio-clean.sh preview|apply
set -euo pipefail
ROOT=/1tbdisk/achewiw
BRANCH=feature/technical-epub-first
ACTION=preview
if [[ $# -gt 0 ]]; then ACTION="$1"; fi
die() { echo "ACHEWIW clean-update: ОСТАНОВЛЕНО: $*" >&2; exit 1; }
msg() { echo "ACHEWIW clean-update: $*"; }
[[ $# -le 1 && ( "$ACTION" == preview || "$ACTION" == apply ) ]] ||
  die "Команды: preview|apply"
[[ -d "$ROOT" && ! -L "$ROOT" && -d "$ROOT/.git" &&
   ! -L "$ROOT/.git" && -f "$ROOT/compose.yaml" &&
   ! -L "$ROOT/compose.yaml" ]] || die "Неверный каталог ACHEWIW"
cd "$ROOT"
[[ "$(git rev-parse --show-toplevel)" == "$ROOT" ]] || die "Неверный корень Git"
[[ "$(git branch --show-current)" == "$BRANCH" ]] || die "Ожидается ветка $BRANCH"
origin="$(git remote get-url origin)" || die "Нет Git origin"
case "$origin" in
  https://github.com/musikfed/achewiw|https://github.com/musikfed/achewiw.git|\
git@github.com:musikfed/achewiw|git@github.com:musikfed/achewiw.git) ;;
  *) die "Чужой Git origin" ;;
esac
if [[ -n "$(git status --porcelain)" ]]; then
  git status --short
  die "Есть локальные изменения. Никаких reset/clean"
fi
[[ -d "$ROOT/data" && ! -L "$ROOT/data" ]] || die "Неверный каталог data/"
for item in "$ROOT/app" "$ROOT/tests" "$ROOT/scripts"; do
  [[ -d "$item" && ! -L "$item" ]] || die "Недопустимый каталог или ссылка: $item"
done
command -v python3 >/dev/null || die "Python3 нужен для read-only проверки"

check_idle() {
  python3 - "$ROOT/data/achewiw.sqlite3" <<'PY'
import sqlite3, sys, time
from pathlib import Path
path=Path(sys.argv[1])
if not path.is_file() or path.is_symlink():
    raise SystemExit("ОСТАНОВЛЕНО: SQLite ACHEWIW отсутствует или является ссылкой")
conn=sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=3)
tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
if "ai_jobs" not in tables:
    raise SystemExit("ОСТАНОВЛЕНО: таблица ai_jobs не найдена")
text=conn.execute("SELECT count(*) FROM ai_jobs WHERE status IN ('queued','running')").fetchone()[0]
image=0
if "achewiw_gpu_lease" in tables:
    image=conn.execute("SELECT count(*) FROM achewiw_gpu_lease WHERE expires_at>?",
                       (time.time(),)).fetchone()[0]
print(f"Текстовых задач: {text}; зарезервированных графических: {image}")
if text or image:
    raise SystemExit("ОСТАНОВЛЕНО: сначала дождись окончания активной генерации")
PY
}

msg "Каталог: $ROOT; ветка: $BRANCH."
msg "Защищены: SQLite, data/, .env, рукописи, ComfyUI, Ollama, NVIDIA и другие проекты."
msg "Обновим страницу через новую версию ресурсов editorial-v8; браузерное хранилище не стираем."
msg "Docker studio будет пересобран без старых слоёв build cache, но без глобального prune."
if [[ "$ACTION" == preview ]]; then
  msg "PREVIEW — ничего не меняем"
  check_idle
  git status -sb
  msg "Для применения: bash scripts/update-studio-clean.sh apply"
  exit 0
fi
check_idle
msg "1/5 — скачиваем новые изменения безопасно"
git fetch --no-tags origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
git merge --ff-only "origin/$BRANCH"
msg "2/5 — проверяем новую версию"
for script in scripts/achewiw-control.sh scripts/monitor-v2.sh scripts/comfyui-local.sh; do
  bash -n "$script" || die "Неверный Bash-синтаксис в $script"
done
test -f app/static/illustrations.js
test -f app/static/writing_blocks.js
test -f app/gpu_scheduling.py
grep -Fq '20261104-editorial-v8' app/static/index.html ||
  die "Нет новых ассетов; отмена пересборки"
msg "3/5 — удаляем только временные .pyc под app/ и tests/"
find -P "$ROOT/app" "$ROOT/tests" -type f -name '*.pyc' -delete
find -P "$ROOT/app" "$ROOT/tests" -depth -type d -name '__pycache__' -empty -delete
msg "4/5 — пересборка без кэша только подтверждённого контейнера ACHEWIW"
bash scripts/achewiw-control.sh rebuild-fresh
msg "5/5 — проверяем веб-приложение и мониторинг"
bash scripts/achewiw-control.sh auth-check
if bash scripts/monitor-v2.sh start; then
  msg "Монитор CPU/RAM/GPU запущен"
else
  msg "Монитор не запущен, смотри scripts/monitor-v2.sh status"
fi
bash scripts/achewiw-control.sh status
msg "Готово. Нажми Ctrl+Shift+R в браузере; data/, SQLite и остальные проекты сохранены."
