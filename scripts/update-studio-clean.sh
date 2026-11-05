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
msg "Проверим версионированные ресурсы сайта после обновления; браузерное хранилище не стираем."
msg "Docker studio будет пересобран без старых слоёв build cache, но без глобального prune."
if [[ "$ACTION" == preview ]]; then
  msg "PREVIEW — ничего не меняем"
  check_idle
  if [[ -f scripts/story-storage-maintenance.py ]]; then
    python3 scripts/story-storage-maintenance.py preview
  fi
  git status -sb
  msg "Для применения: bash scripts/update-studio-clean.sh apply"
  exit 0
fi
check_idle
msg "1/5 — скачиваем новые изменения безопасно"
# Bash has already parsed this running script. If Git updates the updater itself,
# the old process MUST NOT validate new assets with its outdated release marker.
updater_before="$(git hash-object scripts/update-studio-clean.sh)"
git fetch --no-tags origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
git merge --ff-only "origin/$BRANCH"
updater_after="$(git hash-object scripts/update-studio-clean.sh)"
if [[ "$updater_before" != "$updater_after" ]]; then
  msg "Сам обновлятор изменился: перезапускаем новую версию и повторяем безопасные проверки"
  exec bash "$ROOT/scripts/update-studio-clean.sh" apply
fi
msg "2/5 — проверяем новую версию"
for script in scripts/achewiw-control.sh scripts/monitor-v2.sh scripts/comfyui-local.sh scripts/comfyui-control-agent.sh; do
  bash -n "$script" || die "Неверный Bash-синтаксис в $script"
done
test -f app/static/illustrations.js
test -f scripts/comfyui_control_agent.py
python3 - <<'PY'
import ast
from pathlib import Path
for name in ('app/main.py', 'scripts/comfyui_control_agent.py'):
    ast.parse(Path(name).read_text(encoding='utf-8'), filename=name)
PY
test -f app/static/writing_blocks.js
test -f app/gpu_scheduling.py
test -f app/editor_workspace.py
test -f app/book_layout_core.py
python3 -m py_compile scripts/story-storage-maintenance.py app/editor_workspace.py app/book_layout_core.py app/manuscript_core.py app/publishing_exports.py
# Verify the VERSION ACTUALLY DECLARED by index.html, not a hardcoded version
# from a potentially older updater. All five local assets must exist and share
# the same cache-busting version; no browser storage is wiped.
python3 - <<'PY'
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

expected = {
    "/static/style.css", "/static/app.js", "/static/monitor.js",
    "/static/writing_blocks.js", "/static/illustrations.js",
}

class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "link" and a.get("rel") == "stylesheet":
            self.urls.append(a.get("href", ""))
        elif tag == "script" and a.get("src"):
            self.urls.append(a["src"])

parser = AssetParser()
parser.feed(Path("app/static/index.html").read_text(encoding="utf-8"))
if len(parser.urls) != len(expected):
    raise SystemExit("ОСТАНОВЛЕНО: неожиданное число версионированных ресурсов")
paths, versions = set(), set()
for url in parser.urls:
    parsed = urlsplit(url)
    path = parsed.path
    version = parse_qs(parsed.query).get("v", [])
    if path not in expected or not (Path("app") / path.lstrip("/")).is_file():
        raise SystemExit("ОСТАНОВЛЕНО: отсутствует или неизвестен статический ресурс")
    if len(version) != 1 or not version[0] or len(version[0]) > 80:
        raise SystemExit("ОСТАНОВЛЕНО: отсутствует версия ресурса")
    paths.add(path)
    versions.add(version[0])
if paths != expected or len(versions) != 1:
    raise SystemExit("ОСТАНОВЛЕНО: несовпадение версий JS/CSS")
print("ACHEWIW clean-update: Статические ресурсы согласованы: " + next(iter(versions)))
PY
msg "3/5 — делаем безопасную миграцию/обновление поискового кэша"
python3 scripts/story-storage-maintenance.py apply || die "Проверка/индексирование SQLite не прошла; сборка остановлена, существующие книги не удалены"
msg "Очищаем только временные .pyc под app/ и tests/"
find -P "$ROOT/app" "$ROOT/tests" -type f -name '*.pyc' -delete
find -P "$ROOT/app" "$ROOT/tests" -depth -type d -name '__pycache__' -empty -delete
msg "4/5 — пересборка без кэша только подтверждённого контейнера ACHEWIW"
bash scripts/achewiw-control.sh rebuild-fresh
msg "5/5 — проверяем веб-приложение и мониторинг"
bash scripts/achewiw-control.sh auth-check
if bash scripts/comfyui-control-agent.sh reload; then
  msg "Кнопочное управление ComfyUI подключено"
else
  msg "ВНИМАНИЕ: host-control ComfyUI не обновлён (возможно, занята загрузка SDXL). Проверь scripts/comfyui-control-agent.sh status, затем повтори reload"
fi
if bash scripts/monitor-v2.sh start; then
  msg "Монитор CPU/RAM/GPU запущен"
else
  msg "Монитор не запущен, смотри scripts/monitor-v2.sh status"
fi
bash scripts/achewiw-control.sh status
msg "Готово. Нажми Ctrl+Shift+R в браузере; data/, SQLite и остальные проекты сохранены."
