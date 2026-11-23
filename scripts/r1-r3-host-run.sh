#!/usr/bin/env bash
# R1-R3 ACHEWIW host evaluation: controlled Studio-only deployment.
# Use only in /1tbdisk/achewiw on the user's own experimental host.
# Never prunes Docker, edits project books, copies 13GB ComfyUI runtime,
# stops other services, or uploads confidential logs.
set -Eeuo pipefail

ROOT="/1tbdisk/achewiw"
REMOTE="fix/r3-docwork-durable-sessions-20261123"
REPORT_DIR="$(mktemp -d /tmp/achewiw-r123-qa.XXXXXXXX)"
chmod 700 "$REPORT_DIR"
LOG="$REPORT_DIR/host-run.log"
SMOKE="$REPORT_DIR/smoke.log"
REPORT="$REPORT_DIR/report.md"
touch "$LOG" "$SMOKE"
chmod 600 "$LOG" "$SMOKE"
STAGE="preflight"
RESULT="FAIL"
PREVIOUS="unrecorded"
NEW_SHA="unrecorded"

finish() {
  local status="$?"
  trap - EXIT
  if [[ "$status" -eq 0 ]]; then RESULT="PASS"; fi
  {
    echo "# ACHEWIW R1–R3 · испытания на Huananzhi"
    echo
    echo "- Итог: **$RESULT**"
    echo "- Этап завершения: $STAGE"
    echo "- Ветка для испытания: $REMOTE"
    echo "- Прежний коммит: $PREVIOUS"
    echo "- Испытанный коммит: $NEW_SHA"
    echo "- Данные книг и соседние приложения: ни один сценарий не обращается к ним напрямую"
    echo "- Документы: искусственные; тестовый черновик удаляется только по ID и ключу"
    echo "- Полные технические логи: остаются только локально на сервере"
    echo
    echo '## Проверки'
    echo
    if [[ -s "$SMOKE" ]]; then
      sed -n '/^PASS /p;/^FAIL /p;/^R1-R3 HOST SMOKE /p' "$SMOKE" |
        sed 's/^/- /'
    else
      echo "- Проверки в контейнере не были запущены"
    fi
    echo
    echo "GitHub Actions CI — отдельная проверка, не замена хостовой."
  } > "$REPORT"
  chmod 600 "$REPORT"

  echo
  echo "===== SANITIZED REPORT ====="
  cat "$REPORT"
  echo "Локальные подробности: $LOG"
  echo "Синтетические проверки: $SMOKE"

  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    if gh pr comment 26 --repo musikfed/achewiw --body-file "$REPORT" >/dev/null 2>&1; then
      echo "Отчёт добавлен в GitHub PR №26."
    else
      echo "Не удалось отправить отчёт в GitHub. Он напечатан выше — пришлите его в чат."
    fi
  else
    echo "GitHub CLI не авторизован или отсутствует. Пришлите блок SANITIZED REPORT в чат,"
    echo "чтобы его можно было закрепить комментарием к PR №26 без секретов."
  fi
  exit "$status"
}
trap finish EXIT
# Show each step live while keeping full technical output only in local /tmp.
exec > >(tee -a "$LOG") 2>&1

{
  echo "=== R1-R3 HOST TEST ==="
  echo "Проверяем собственный рабочий каталог и безопасную ветку."
  [[ -d "$ROOT/.git" && ! -L "$ROOT/.git" ]] || {
    echo "Прекращено: нет ожидаемого git checkout"; exit 2;
  }
  cd "$ROOT"
  [[ "$(git rev-parse --show-toplevel)" == "$ROOT" ]] || {
    echo "Прекращено: не корень ACHEWIW"; exit 2;
  }
  origin="$(git remote get-url origin)"
  case "$origin" in
    https://github.com/musikfed/achewiw|https://github.com/musikfed/achewiw.git|git@github.com:musikfed/achewiw|git@github.com:musikfed/achewiw.git)
      ;;
    *) echo "Прекращено: непроверенный origin"; exit 2;;
  esac

  if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
    echo "Прекращено: есть локальные изменения/файлы — без reset/clean не продолжаем."
    git status --short
    exit 2
  fi
  PREVIOUS="$(git rev-parse HEAD)"
  echo "Прежний коммит: $PREVIOUS"

  command -v docker >/dev/null || { echo "Прекращено: Docker отсутствует"; exit 2; }
  docker info >/dev/null 2>&1 || { echo "Прекращено: нет доступа к Docker"; exit 2; }

  # Root was previously 93% full. Refuse a new image build if dangerously low.
  free_kb="$(df -Pk / | awk 'END {print $4}')"
  if [[ ! "$free_kb" =~ ^[0-9]+$ || "$free_kb" -lt 4194304 ]]; then
    echo "Прекращено: на корневом разделе менее 4 GiB; Docker-образы не строим."
    exit 2
  fi
  echo "До сборки свободно на /: $((free_kb / 1024)) MiB"

  STAGE="fetch"
  git fetch --no-tags origin \
    "+refs/heads/$REMOTE:refs/remotes/origin/$REMOTE"
  NEW_SHA="$(git rev-parse "refs/remotes/origin/$REMOTE")"
  git cat-file -e "$NEW_SHA:scripts/r1-r3-host-smoke.py" || exit 2
  git cat-file -e "$NEW_SHA:app/docwork_sessions.py" || exit 2
  echo "Проверенная тестовая ветка: $NEW_SHA"

  STAGE="checkout"
  # No --force: uncommitted/untracked changes are checked above.
  git switch --detach "$NEW_SHA"
  [[ "$(git rev-parse HEAD)" == "$NEW_SHA" ]] || exit 2
  echo "Переключен только исходный код ACHEWIW, локальные data/ не тронуты."

  STAGE="build-studio"
  # Existing lifecycle script verifies labels, Compose project, workspace and
  # ownership before recreating ONLY achewiw-studio. No docker compose down.
  bash scripts/achewiw-control.sh rebuild

  STAGE="smoke"
  echo "Проверяем R1 + R2 + R3 в работающем контейнере."
  docker exec -i -w /app achewiw-studio python - \
    < "$ROOT/scripts/r1-r3-host-smoke.py" | tee "$SMOKE"
  grep -Fq 'R1-R3 HOST SMOKE PASS' "$SMOKE" || exit 3

  STAGE="healthy"
  bash scripts/achewiw-control.sh auth-check
  docker compose ps studio

  STAGE="complete"
  echo "Успешно: испытательная версия R1–R3 запущена и проверена."
  echo "Откройте http://192.168.0.100:7080/docwork/ в браузере через LAN/VPN."
  echo "Проверьте F5, две вкладки и ручное редактирование на ИСКУССТВЕННОМ документе."
}

# Print only the sanitized report to console. Raw Docker logs are local.
