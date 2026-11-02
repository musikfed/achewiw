#!/usr/bin/env bash
# ACHEWIW: move only this repository out of PegineLab/projects.
# Never starts Docker/Ollama or deletes project data.
set -Eeuo pipefail

DISK="/1tbdisk"
OLD="$DISK/PegineLab/projects/achewiw"
NEW="$DISK/achewiw"
REPO="https://github.com/musikfed/achewiw.git"
BRANCH="feature/technical-epub-first"

fail() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }
exists() { [[ -e "$1" || -L "$1" ]]; }

[[ -d "$DISK" && ! -L "$DISK" ]] || fail "Не найден обычный каталог $DISK"
cd "$DISK"

# Refuse to move files used by a running ACHEWIW container.
if command -v docker >/dev/null 2>&1; then
  running="$(docker ps --format '{{.Names}}' 2>/dev/null || true)"
  if printf '%s\n' "$running" | grep -Fxq 'achewiw-studio'; then
    fail "Контейнер achewiw-studio запущен. Не перемещаем каталог работающего проекта."
  fi
fi

if exists "$OLD"; then
  [[ -d "$OLD" && ! -L "$OLD" ]] || fail "Источник $OLD не обычная папка"
  [[ -d "$DISK/PegineLab/projects" ]] || fail "Отсутствует родительский каталог источника"
fi
if exists "$NEW"; then
  [[ -d "$NEW" && ! -L "$NEW" ]] || fail "Назначение $NEW существует, но не является обычной папкой"
fi

if exists "$OLD" && ! exists "$NEW"; then
  mv -T --no-clobber -- "$OLD" "$NEW"
  [[ ! -e "$OLD" && -d "$NEW" ]] || fail "Перемещение не завершилось; проверьте обе папки"
  echo "Перенесено: $OLD -> $NEW"
elif exists "$OLD" && exists "$NEW"; then
  # Never merge/destroy. Move the old copy out of PegineLab/projects.
  stamp="$(date +%Y%m%d-%H%M%S)-$$"
  archive="$DISK/achewiw.previous.$stamp"
  [[ ! -e "$archive" && ! -L "$archive" ]] || fail "Архив уже существует: $archive"
  mv -T --no-clobber -- "$OLD" "$archive"
  [[ ! -e "$OLD" && -d "$archive" ]] || fail "Не удалось архивировать старый каталог"
  echo "Новая папка уже существовала. Не заменяли её: $NEW"
  echo "Старый каталог сохранён в архиве: $archive"
  echo "ВАЖНО: перед удалением архива сравните его .env, data/ и незакоммиченные изменения."
elif exists "$NEW"; then
  echo "Уже на нужном месте: $NEW. Старой папки в PegineLab/projects нет."
else
  git clone --branch "$BRANCH" --single-branch "$REPO" "$NEW"
  [[ -d "$NEW/.git" && -f "$NEW/compose.yaml" ]] || fail "Клонирование требует проверки: $NEW"
  echo "Скачано прямо в $NEW из $REPO (ветка $BRANCH)"
fi

if [[ -d "$NEW/.git" ]]; then
  git -C "$NEW" status -sb
  git -C "$NEW" log -1 --oneline
fi
printf '\nГотово: только ACHEWIW. Папки YadroKreator и wificam_T23N не затрагивались.\n'
printf 'Docker/Ollama не запускались, файлы проекта не удалялись.\n'