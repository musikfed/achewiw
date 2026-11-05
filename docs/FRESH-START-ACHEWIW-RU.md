# ACHEWIW: чистый старт с нуля после GitHub-обновления

Этот сценарий предназначен **только** для проекта `musikfed/achewiw`, ветка
`feature/technical-epub-first`, Ubuntu-каталог `/1tbdisk/achewiw`.

**ОСТОРОЖНО:** это необратимое удаление всей прежней книжной библиотеки,
включая исходные проекты, принятые/непринятые рукописи, AI jobs/QA, картинки,
все три SQLite и их локальные архивы с известными префиксами. Архивы
`achewiw-before-...` и `before-story-storage-...` **тоже удаляются**.
Если одна старая книга ещё нужна, сначала скопируй её **за пределы**
`/1tbdisk/achewiw/data` вручную. Новый пустой проект появится только после
нажатия «+ Новая работа».

## Что очищается

- `data/achewiw.sqlite3`, `data/search.sqlite3`, `data/work.sqlite3` и
  их временные SQLite WAL/SHM/journal.
- Все `data/illustrations/<id>` (старые обложки и рисунки).
- Свои SQLite архивы из `data/backups` с проверенными префиксами.
- `*.pyc`, `__pycache__`, `.pytest_cache` внутри исходников ACHEWIW.
- Только браузерные ключи `achewiw.*` (на следующем открытии сайта)
  благодаря новому `.achewiw-library-epoch`. Трекер текущей операции
  ComfyUI сохраняется, потому что это может быть большая загрузка SDXL.
- Студийный Docker image **собирается без использования прежних build layers**
  через `docker compose build --no-cache studio`. Глобальный Docker
  кэш и чужие образы при этом НЕ удаляются.

## Что остаётся без изменения

`data/comfyui-runtime/` (установленный ComfyUI и SDXL-модель),
`data/comfyui-control.token` и текущий host-control, `.env`,
`data/monitor/`, общий хостовый Ollama, другие сервисы, другие папки
`/1tbdisk`, другие Docker-контейнеры/образы, системный Python и systemd.
Никакого `git clean`, `git reset --hard`, `docker system prune`.

## Порядок запуска на Ubuntu (без sudo)

Подключись по SSH под пользователем, которому принадлежит
`/1tbdisk/achewiw`. **Не начинай сброс при активной генерации текста или
картинок.** Чтобы получить новую версию сценария, сначала обнови рабочую
ветку fast-forward:

```bash
cd /1tbdisk/achewiw
git branch --show-current
git status -sb

# Ожидаем feature/technical-epub-first и чистый Git status.
git fetch --no-tags origin
git merge --ff-only origin/feature/technical-epub-first

# Никаких удалений: показывает точные пути и останавливается, если есть jobs.
bash scripts/reinstall-studio-fresh.sh preview
```

Если список соответствует ожидаемому и ты действительно хочешь потерять
все старые книги/архивы, запускай одну команду:

```bash
bash scripts/reinstall-studio-fresh.sh ERASE_ACHEWIW_LIBRARY_AND_ARCHIVES
```

Сценарий последовательно: повторно проверяет репозиторий, ветку, Docker
identity/Compose labels и текущие задачи → fast-forward GitHub → выключает
только Studio → удаляет книжные БД и изображения → очищает Python caches →
пишет маркер нового поколения браузера → собирает Studio без Docker cache →
запускает Studio → проверяет 200 OK без HTTP Basic → проверяет пустоту
`projects`, `ai_jobs`, `illustrations`, поисковых записей и
рабочих предложений. Если что-то не совпало, остановится с ошибкой.

## Контроль результата

```bash
cd /1tbdisk/achewiw
bash scripts/achewiw-control.sh status
bash scripts/achewiw-control.sh auth-check

# Проверка на пустую библиотеку (через LAN/VPN):
curl -fsS --noproxy '*' http://192.168.0.100:7080/api/projects

# Список и размер новых SQLite:
ls -lh data/achewiw.sqlite3 data/search.sqlite3 data/work.sqlite3
```

В браузере открой `http://192.168.0.100:7080/` и сделай
**Ctrl+Shift+R**. Проекты должны отсутствовать; старые черновики из
localStorage/sessionStorage ACHEWIW автоматически отвяжутся от нового
поколения библиотеки. Нажми «+ Новая работа», сохрани редакционное
задание, создай каркас, книгу и иллюстрации заново.

Если скрипт останавливается **до удаления**, проверь сообщение об ошибке и
не запускай вручную `rm -rf data` — там лежат локальные модели и контроллер.
Если он остановился **после удаления**, данные не восстановятся из
удалённых локальных архивов; потребуется устранить проблему сборки
и повторно запустить только Studio, без повторного удаления.
