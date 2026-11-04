# ACHEWIW: очистить старые рассказы/книги и заново собрать свою Studio

Это **не** команда YadroKreator! Все действия проходят в `/1tbdisk/achewiw` и касаются только Docker Compose-проекта `achewiw`, FastAPI Studio и его SQLite. Ollama остаётся на хосте и не перезапускается. Никакого общего `docker prune`.

## Первый раз: обновить скрипт на Ubuntu

После объединения PR в `feature/technical-epub-first`:

```bash
cd /1tbdisk/achewiw
git status --short
bash scripts/achewiw-control.sh status
bash scripts/achewiw-control.sh update
```

Убедись, что выбран `feature/technical-epub-first`, а не `main` (в main пока нет всей Studio). Существующий скрипт `update` делает fast-forward, не удаляет локальные изменения или книги.

## Увидеть, что будет удалено

```bash
cd /1tbdisk/achewiw
bash scripts/rebuild-library.sh --check --reset-library --no-cache
```

`--check` показывает состояния контейнера и список проектов/времён из SQLite; **ничего не удаляет**. При отсутствии `data/achewiw.sqlite3` сообщит об этом. Не удаляет SQLite из других каталогов.

## Запустить обновление, очистку и пересборку (после просмотра)

```bash
bash scripts/rebuild-library.sh --apply --reset-library --no-cache
```

Порядок: остановка только подтверждённого ACHEWIW → обновление Git → проверка отсутствия запущенного ACHEWIW → консистентная копия SQLite `data/backups/achewiw-before-reset-*.sqlite3` → очистка таблиц **projects**, **ai_jobs**, **ai_job_reviews** → только `docker compose --project-name achewiw ... build --no-cache studio` → запуск только Studio → HTTP-проверка работы без HTTP Basic.

**Важно:** `--reset-library` удалит **ВСЕ записи** проектов, книг, рассказов, технических книг, выступлений и задач AI в текущей ACHEWIW SQLite — не только отдельные старые названия. Это выбор для полного начала с чистой библиотеки. Если нужно сохранить хотя бы один проект, воспользуйся `python3 scripts/library_admin.py --list` и `--purge-project ID --confirm DELETE_PROJECT` отдельно вместо общего сброса.

**Путь отката данных:** резервная копия остаётся в `/1tbdisk/achewiw/data/backups/`. Не заменяй текущую SQLite при работающем контейнере; восстановление следует выполнять только в остановленном состоянии, с дополнительной копией текущей базы. В данной операции никакой архив автоматически не удаляется.

`--no-cache` **не удаляет** общий BuildKit/Docker cache с сервера, а заново выполняет стадии сборки только образа `studio`. Это может занимать больше времени и диска. Остальные контейнеры и их volumes не трогаются. Если оставить `--no-cache`, Compose переиспользует свой кэш. Если убрать `--reset-library`, библиотека остаётся прежней.

## Контроль

```bash
bash scripts/achewiw-control.sh status
bash scripts/achewiw-control.sh auth-check
python3 scripts/library_admin.py --list
```

В браузере на защищённой LAN/VPN `http://192.168.0.100:7080` откроется чистая библиотека (при успешном сбросе). **Ни YadroKreator, ни WIFICAM, ни WifCam Helper тут не пересобираются и не очищаются.**
