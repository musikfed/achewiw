# ACHEWIW — GitHub ↔ сервер Ubuntu (2 операции)

Эта памятка касается **только исходного кода** проекта в `/1tbdisk/PegineLab/projects/achewiw`. Команды **не запускают Docker**, не трогают Ollama и не требуют загрузки LLM. Выбранную Git-ветку проверяйте перед каждым обновлением: экспериментальные RFC-ветки ещё не являются готовым релизом.

## 1. Из GitHub → на сервер (скачать / обновить код)

### Полностью заменить старую папку чистой копией (без запуска)

**Рекомендуется при необходимости «затереть старую версию» на сервере.** Вместо необратимых `reset --hard`, `git clean -fdx` и `rm -rf` сохраняем всю прежнюю папку (включая локальные правки, `.env` и `data/`) в резервную копию, а затем клонируем чистый GitHub checkout. Если `git clone` не удался, резервная копия остаётся на месте.

```bash
cd /1tbdisk/PegineLab/projects

# Старые исходники, локальные настройки и база остаются в резервной папке:
BACKUP="achewiw.backup.$(date +%Y%m%d-%H%M%S)"
mv -- achewiw "$BACKUP"

# Актуальная ветка разработки: веб-MVP + экспериментальная архитектура EPUB:
git clone \
  --branch feature/technical-epub-first \
  --single-branch \
  https://github.com/musikfed/achewiw.git achewiw

# Проверить, что получены именно исходники, а не одна документация:
cd achewiw
git status -sb
git log -1 --oneline
test -f compose.yaml && echo "Исходники веб-проекта получены"
ls -la
```

**Важно о ветках:** дефолтная ветка `main` пока содержит преимущественно начальную документацию и **не содержит `compose.yaml`**. Поэтому для полной текущей версии с кодом используем `feature/technical-epub-first` (экспериментальная ветка, не полноценный релиз); исходный веб-MVP — `feature/storyengine-mvp`. Не утверждать, будто все запланированные функции уже реализованы. Если захотите только MVP, в команде `git clone` замените ветку на `feature/storyengine-mvp`.

Папку `$BACKUP` не удалять до ручной проверки содержимого новой папки и выбора момента переноса необходимых локальных данных. Не переносить старые `.env`, БД или сертификаты в публичный GitHub. Если старый контейнер запущен, эти команды **не останавливают** его и **не обновляют** запущенный сервис — это только работа с исходниками.

UFW: после `sudo ufw allow 7080` сообщение `Skipping adding existing rule` означает, что разрешение для порта уже существует; повторять команду не требуется. **Правило firewall не означает, что приложение уже запущено.** Запуск Docker и скрипт развёртывания — **отдельный этап позже**, не выполнять `docker compose up` сейчас.

**Если на сервере репозитория ещё нет:**

```bash
cd /1tbdisk/PegineLab/projects
git clone https://github.com/musikfed/achewiw.git
cd achewiw
git fetch origin
# Для изучения последней архитектуры (RFC, пока не релиз):
git switch --track origin/feature/technical-epub-first
git status -sb
```

Для версии с первоначальным веб-MVP вместо последней команды переключения можно выбрать `git switch --track origin/feature/storyengine-mvp`. При переходе на другую ветку не перепутайте документацию/RFC с внедрёнными функциями.

**Если репозиторий уже есть на сервере:**

```bash
cd /1tbdisk/PegineLab/projects/achewiw
git status --short                # сначала проверьте локальные правки
git branch --show-current         # какая ветка активна
git fetch origin
git pull --ff-only                # обновить текущую отслеживаемую ветку
git log -1 --oneline
```

Если `git status --short` выводит локальные изменения или `pull --ff-only` сообщает о расхождении истории, **остановитесь**: сначала сохраните правки отдельным коммитом/веткой либо вручную разберите конфликт. Не используйте `reset --hard` и `git clean -fd` — они могут стереть работу.

> Конфигурация `.env`, база `data/` и файлы сертификатов остаются **на сервере**, а не в GitHub. В проекте `.env` и `data/` исключены через `.gitignore`; всё равно проверяйте подготовленные файлы перед push.

## 2. С сервера → в GitHub (сохранить свои правки)

Лучше публиковать изменения через **свою ветку и Pull Request**, не писать сразу в `main`:

```bash
cd /1tbdisk/PegineLab/projects/achewiw
git status --short
git branch --show-current
# Создать отдельную ветку от текущей основы:
git switch -c feature/server-edits
# Добавьте только нужные исходники, например:
git add README.md docs/
git diff --cached --check
git diff --cached                 # проверьте, что нет паролей, ключей и данных
git commit -m "docs: update ACHEWIW server notes"
git push -u origin feature/server-edits
```

После push откройте репозиторий https://github.com/musikfed/achewiw и создайте Pull Request из `feature/server-edits` **в ту ветку, от которой начинали** (например, `feature/technical-epub-first`). После проверки изменения можно объединить в нужную ветку.

- Имена `feature/server-edits` и сообщение коммита — примеры: для следующей партии правок создавайте новое, уникальное имя ветки.
- `git commit` не создаст коммит без подготовленных изменений; в таком случае `git status` поможет разобраться.
- Для HTTPS push GitHub может потребовать настроенный credential manager / Personal Access Token (не вводите токен в URL командной строки). Альтернатива — SSH-ключ в учётной записи GitHub.
- **Никогда не отправляйте** `.env`, `privkey.pem`, `fullchain.pem`, ключи, токены, личные рукописи или рабочую БД в публичный репозиторий. `git add .` и `git add -A` без проверки здесь не рекомендуются.
