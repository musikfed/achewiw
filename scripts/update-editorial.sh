#!/usr/bin/env bash
# Refresh ONLY the ACHEWIW visual release. No Docker prune, no DB cleanup.
# Use: cd /1tbdisk/achewiw && bash scripts/update-editorial.sh
set -euo pipefail

ROOT="/1tbdisk/achewiw"
BRANCH="feature/technical-epub-first"
EXPECTED="musikfed/achewiw"

die() { printf 'ACHEWIW: ОСТАНОВЛЕНО — %s\n' "$*" >&2; exit 1; }

[[ -d "$ROOT" && ! -L "$ROOT" ]] || die "Рабочий каталог не найден или является ссылкой"
cd "$ROOT"
[[ "$(git rev-parse --show-toplevel)" == "$ROOT" ]] || die "Это не корень репозитория ACHEWIW"
[[ "$(git branch --show-current)" == "$BRANCH" ]] || die "Нужна ветка $BRANCH"
case "$(git remote get-url origin)" in
  https://github.com/$EXPECTED|https://github.com/$EXPECTED.git|\
git@github.com:$EXPECTED|git@github.com:$EXPECTED.git) ;;
  *) die "Незнакомый Git origin; другие проекты не обновляем" ;;
esac
[[ -z "$(git status --porcelain)" ]] || {
  git status --short
  die "Есть локальные изменения: не делаем reset/clean и не затираем их"
}

printf '\n=== 1/4: безопасное получение новой версии ===\n'
git fetch --no-tags origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
git merge --ff-only "origin/$BRANCH"

printf '\n=== 2/4: проверка новых ресурсов ===\n'
test -f app/static/index.html
test -f app/static/style.css
test -f app/static/app.js
test -f scripts/monitor-v2.sh
grep -Fq "editorial-v6" app/static/index.html || die "Нет новой версии ассетов"
bash -n scripts/achewiw-control.sh
bash -n scripts/monitor-v2.sh

printf '\n=== 3/4: пересборка только своего Studio (без общего prune) ===\n'
bash scripts/achewiw-control.sh rebuild

printf '\n=== 4/4: проверка отсутствия HTTP Basic ===\n'
bash scripts/achewiw-control.sh auth-check
bash scripts/achewiw-control.sh status

printf '\n=== Хостовый монитор без доступа к чужому user-unit ===\n'
if bash scripts/monitor-v2.sh start; then
  echo "ACHEWIW: монитор v2 запущен."
else
  echo "ВНИМАНИЕ: сборщик CPU/RAM/GPU не запущен. Диагностика: bash scripts/monitor-v2.sh status" >&2
fi

printf '\nACHEWIW: свежий редакционный дизайн загружен.\n'
printf 'Откройте страницу с обновлением Ctrl+Shift+R, если вкладка открыта.\n'
printf 'SQLite, книги, .env, Ollama и соседние проекты не изменены.\n'
