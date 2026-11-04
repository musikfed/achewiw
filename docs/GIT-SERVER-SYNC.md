# ACHEWIW — GitHub ↔ Ubuntu: перенос и синхронизация (без запуска)

**Проект:** https://github.com/musikfed/achewiw  
**Единственное штатное расположение на сервере:** `/1tbdisk/achewiw`  
**Прежнее расположение (только для переноса):** `/1tbdisk/PegineLab/projects/achewiw`

`/1tbdisk/PegineLab/projects` содержит независимые проекты `YadroKreator` и `wificam_T23N`; **не очищайте и не удаляйте** ни этот каталог, ни соседние проекты. Все обычные операции Git для ACHEWIW выполняйте только в `/1tbdisk/achewiw`.

Команды ниже **не запускают** Docker, приложение, Ollama или LLM. Порт `7080` относится к будущему веб-приложению, но перенос исходников сам по себе ничего не открывает и не запускает.

## 0. Один раз: перенести только ACHEWIW в /1tbdisk (без запуска)

**Путь после переноса:** `/1tbdisk/achewiw`. Если папка уже перенесена и старый путь отсутствует, **ничего больше переносить не нужно**.

На данном сервере обычный пользователь `user` не имеет прав записи в корневой каталог `/1tbdisk` (при попытке `curl -o /1tbdisk/...` возникло `Permission denied`). Поэтому для переноса используется **одна узкая команда `sudo mv`**, а не запуск скачанного скрипта с правами root. Не меняйте права на `/1tbdisk` и не используйте `sudo curl | bash`.

```bash
OLD="/1tbdisk/PegineLab/projects/achewiw"
NEW="/1tbdisk/achewiw"

if [ -d "$OLD" ] && [ ! -L "$OLD" ] && [ ! -e "$NEW" ] && [ ! -L "$NEW" ]; then
    sudo mv -T --no-clobber -- "$OLD" "$NEW"
elif [ -d "$NEW" ] && [ ! -e "$OLD" ] && [ ! -L "$OLD" ]; then
    echo "ACHEWIW уже на нужном месте, повторять перенос не нужно"
else
    echo "ОСТАНОВЛЕНО: обе папки существуют, либо исходный путь нестандартный. Ничего не изменено."
fi

ls -ld /1tbdisk/achewiw
ls /1tbdisk/PegineLab/projects
git -C /1tbdisk/achewiw status -sb
```

**Не трогать** `wificam_T23N`, `YadroKreator` или весь `/1tbdisk/PegineLab/projects`. Если обе папки ACHEWIW существуют, **не удаляйте и не перезаписывайте** ни одну автоматически: сначала сравните содержимое, незакоммиченные изменения, `.env` и `data/`. Если работающий контейнер использует прежний путь, перенос согласуйте отдельно; команды не останавливают его.

**Ранее опубликованный альтернативный скрипт**: [`scripts/move-achewiw-to-root.sh`](../scripts/move-achewiw-to-root.sh). Для ограниченных прав на `/1tbdisk` предпочтителен приведённый выше точечный `sudo mv`, поскольку скачивание скрипта в `/1tbdisk` и перемещение без `sudo` могут закончиться отказом в доступе. После уже выполненного переноса запускать его **не нужно**.

## Актуальный сценарий: обновить работающую мастерскую, сохранив книги

Для уже запущенного ACHEWIW **не требуется стирать весь каталог или повторно клонировать Git**. Действующая ветка `feature/technical-epub-first` обновляется через собственный изолированный сценарий:

```bash
cd /1tbdisk/achewiw
git status -sb                     # если есть локальные изменения — сначала сохранить
bash scripts/achewiw-control.sh update
bash scripts/achewiw-control.sh status
```

`update` проверяет Git origin/ветку, делает только fast-forward и при ранее запущенном `studio` пересобирает **только контейнер ACHEWIW**. `data/`, `.env`, Ollama, GPU-хост, соседние проекты остаются на месте. В новой версии нет обязательного HTTP-пароля, но разрешены только локальная сеть/VPN и доверенные Host. Для вопросов об издательском интерфейсе, EPUB/FB2/DOCX/PDF и очистке базы — [новое руководство](STUDIO-EDITION-RU.md).

Если старая версия управляющего скрипта требует пароль до Git fetch, **не стирать папку**: выполнить `git fetch origin && git merge --ff-only origin/feature/technical-epub-first` (только в чистой рабочей ветке), а затем повторить `bash scripts/achewiw-control.sh update`. При конфликте или грязном Git — остановиться, не делать hard reset.

## 1. GitHub → сервер: получить / обновить код

### Обновление после переноса: с `feature/server-edits` на ветку с приложением

На сервере ранее было `## feature/server-edits...origin/feature/server-edits`. Это отдельная ветка, близкая к `main`, **без `compose.yaml`**. Простой `git pull` внутри неё не скачает рабочие исходники расширенной ветки `feature/technical-epub-first`. Эти ветки разошлись, поэтому **не выполнять `git merge` между ними, `git reset --hard` или `git push --force`**.

Следующий блок сохраняет `feature/server-edits` со всеми её коммитами, только переключает активную рабочую ветку и делает **fast-forward** обновление. Он останавливается, если есть незафиксированные изменения. Не требует `sudo`, не запускает приложение, Docker или Ollama.

```bash
bash <<'BASH'
set -euo pipefail
cd /1tbdisk/achewiw

# Любые незакоммиченные изменения — повод остановиться, не перезаписывать:
if [[ -n "$(git status --porcelain)" ]]; then
  git status --short
  echo "ОСТАНОВЛЕНО: сохраните локальные изменения перед переключением."
  exit 1
fi

git fetch origin
if git show-ref --verify --quiet refs/heads/feature/technical-epub-first; then
  git switch feature/technical-epub-first
else
  git switch --track -c feature/technical-epub-first origin/feature/technical-epub-first
fi

git merge --ff-only origin/feature/technical-epub-first
git status -sb
git log -1 --oneline
test -f compose.yaml && echo "Исходники приложения загружены. Запуска не было."
BASH
```

Если `git merge --ff-only` сообщает, что ветки разошлись, остановитесь и проверьте локальные коммиты, не сбрасывайте их. Чтобы вернуться к прежнему коду без удаления новых файлов: `git switch feature/server-edits` (при условии, что нет мешающих локальных изменений).

Это экспериментальная ветка: архитектура EPUB, типографии и RAG ещё **не равна** реализованным веб-функциям. Для возвращения к базовой версии веб-MVP выберите `feature/storyengine-mvp` отдельно. **Запуск проекта — позже.**



Если переноса не было и папки `/1tbdisk/achewiw` нет:

```bash
git clone --branch feature/technical-epub-first --single-branch 
  https://github.com/musikfed/achewiw.git /1tbdisk/achewiw
```

Если папка уже есть:

```bash
cd /1tbdisk/achewiw
git status --short          # при локальных правках сначала сохранить их
git branch --show-current
git fetch origin
git pull --ff-only          # обновить текущую отслеживаемую ветку
git log -1 --oneline
```

**Ветки:** `main` пока содержит начальную документацию без `compose.yaml`, первоначальный веб-MVP — `feature/storyengine-mvp`, расширенный экспериментальный код и EPUB-RFC — `feature/technical-epub-first`. Проверяйте нужную ветку перед обновлением.

Если `git pull --ff-only` сообщает о конфликте / разошедшейся истории, **не сбрасывайте** коммиты; разберите локальные изменения вручную.

## Мониторинг CPU, видеопамяти NVIDIA и моделей Ollama

**Код находится в ветке `feature/technical-epub-first`, не в документационной `main`.** Панель встроена в существующий веб-интерфейс ACHEWIW на 7080 и через авторизованный `/api/monitoring` показывает CPU, RAM, GPU/VRAM, выбранную LLM и реально загруженные модели Ollama. GPU-снимки делает отдельный **read-only сборщик на Ubuntu-хосте**, а не контейнер с Docker-доступом. Состояние `stale` означает старые показания, а не нулевую загрузку.

Полная инструкция: [docs/MONITORING.md в рабочей ветке](https://github.com/musikfed/achewiw/blob/feature/technical-epub-first/docs/MONITORING.md).

### Как скачать последние изменения без запуска проекта

```bash
bash <<'BASH'
set -euo pipefail
cd /1tbdisk/achewiw

if [[ -n "$(git status --porcelain)" ]]; then
  git status --short
  echo "ОСТАНОВКА: есть незакоммиченные файлы, сначала сохраните их."
  exit 1
fi

git fetch origin
BRANCH="feature/technical-epub-first"
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git switch "$BRANCH"
else
  git switch --track -c "$BRANCH" "origin/$BRANCH"
fi
git merge --ff-only "origin/$BRANCH"

git status -sb
git log -1 --oneline
test -f scripts/achewiw_host_monitor.py
test -f app/monitoring_core.py
test -f app/static/monitor.js
test -f docs/MONITORING.md
echo "Мониторинг скачан. Проект НЕ запускался."
BASH
```

Команды **не требуют sudo**, не меняют `/1tbdisk/PegineLab/projects`, не запускают Docker/Ollama и не запускают мониторинг. Разовый сбор показаний и будущий user-systemd монитор приведены в `docs/MONITORING.md`, но их запуск — **позднее**. Новых портов нет. Если монитор ещё не включён, веб-панель честно покажет отсутствие данных или устаревший снимок.

## 2. Сервер → GitHub: сохранить изменения

```bash
cd /1tbdisk/achewiw
git status --short
git branch --show-current
git switch -c feature/server-edits-$(date +%Y%m%d-%H%M%S)
# Пример выбора только нужных исходников — подставьте реально изменённые файлы:
git add README.md docs/
git diff --cached --check
git diff --cached         # проверка на отсутствие секретов и приватного текста
git commit -m "docs: update ACHEWIW"
git push -u origin HEAD
```

Затем создайте Pull Request в **исходную ветку** проекта на https://github.com/musikfed/achewiw. Новый branch name исключает случайную перезапись старой рабочей ветки; если в выбранных файлах нет изменений, `git commit` не создаст коммит.

**Не отправлять в публичный GitHub:** `.env`, `data/`, `privkey.pem`, `fullchain.pem`, токены, закрытые рукописи и персональные файлы. Для HTTPS-push нужен credential manager/token без встраивания токена в URL либо SSH с настроенным ключом.

## Управление состоянием ACHEWIW без воздействия на другие проекты

После будущего отдельного запуска можно проверить или выключить **только ACHEWIW**:

```bash
cd /1tbdisk/achewiw
bash scripts/achewiw-control.sh status
bash scripts/achewiw-control.sh stop
```

`stop` останавливает **только подтверждённые и зарегистрированные контейнеры ACHEWIW**: совпадают Compose-проект, рабочий каталог `/1tbdisk/achewiw`, собственные метки и allowlist служб (сейчас только `studio`). Если обнаружен неподтверждённый кандидат, выключение отменяется. Остановка веб-контейнера освобождает обслуживаемый им порт 7080; если порт занимает **другой** процесс, скрипт не убивает его. Хостовая Ollama, проекты в `/1tbdisk/PegineLab/projects`, UFW, VPN и другие Docker-проекты не затрагиваются. Скрипт не имеет команды запуска. [Схема служб и ограничения](PROJECT-LIFECYCLE.md); [строгий реестр управляемых компонентов](SERVICE-OWNERSHIP.md). Веб-кнопка «Выключить мастерскую» — отдельная будущая задача.

## Когда будем запускать

Развёртывание и скрипт запуска — **позже и отдельно**, из `/1tbdisk/achewiw`. Оллама остаётся на Ubuntu-хосте, веб-студия позднее слушает 7080; правило UFW уже существует (`Skipping adding existing rule` не ошибка). Сейчас **только перенос/исходники**.
