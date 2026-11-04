# ACHEWIW · запуск, остановка, проверка и обновление на Huananzhi CORE

**Репозиторий:** https://github.com/musikfed/achewiw  
**Рабочая ветка с приложением:** `feature/technical-epub-first`  
**Каталог на Ubuntu:** `/1tbdisk/achewiw` (не путать с PegineLab/YadroKreator)  
**Адрес:** `http://192.168.0.100:7080` — исключительно через доверенную VPN или локальную сеть с защищённым доступом.

## Команды одного управляющего скрипта

```bash
cd /1tbdisk/achewiw
bash scripts/achewiw-control.sh status     # только чтение, свежесть host CPU/RAM/GPU
bash scripts/achewiw-control.sh start      # запуск studio и попытка включить собственный монитор
bash scripts/achewiw-control.sh monitor-start # включить только user-systemd монитор CPU/RAM/NVIDIA
bash scripts/achewiw-control.sh restart    # перезапуск только studio
bash scripts/achewiw-control.sh stop       # остановка только подтверждённых контейнеров
bash scripts/achewiw-control.sh update     # Git fetch + fast-forward; работающий studio пересобирается
```

Скрипт проверяет Docker daemon, метки владельца контейнера, точную рабочую папку, разрешённую службу `studio`, и для запуска — `.env`, `data/` и доступность TCP 7080. Создание и пересборка выполняются **только** из `compose.yaml` этого проекта. Не выполняются `docker compose down -v`, `docker system prune`, `pkill`, удаление SQLite и команда `systemctl stop ollama`.

Если зарегистрирован `achewiw-host-monitor.service`, его можно включать/выключать только после проверки `FragmentPath`, `WorkingDirectory` и `ExecStart`. Неизвестный одноимённый user unit блокирует изменяющие операции. Общие ресурсы Huananzhi, Ollama, NVIDIA, Docker daemon, YadroKreator и другие проекты остаются независимыми.

## Первое обновление ранее установленного ACHEWIW

Если скрипт на сервере пока содержит только `status|stop`, получить новую версию нужно обычным Git:

```bash
cd /1tbdisk/achewiw
git remote -v
git branch --show-current
git status --short
```

Рабочая ветка с реализованным `compose.yaml` — `feature/technical-epub-first`. Если она **уже выбрана** и локальные изменения отсутствуют:

```bash
git fetch origin
git merge --ff-only origin/feature/technical-epub-first
bash -n scripts/achewiw-control.sh
bash scripts/achewiw-control.sh status
```

Если выбрана `feature/server-edits`, `main` или другая ветка, **не пытайтесь сливать её с рабочей автоматически**. Сначала сохраните локальные коммиты и незакоммиченные изменения, затем отдельно переключитесь на `feature/technical-epub-first` согласно [инструкции синхронизации](GIT-SERVER-SYNC.md). Никаких `git reset --hard`, `git clean -fdx`, `git push --force` или удаления чужих веток.

## Подготовить запуск

```bash
cd /1tbdisk/achewiw
test -e .env || cp .env.example .env
mkdir -p data
nano .env
# Задайте непустой длинный ACHEWIW_PASSWORD и правильные UID/GID для data/.
docker compose config --quiet
bash scripts/achewiw-control.sh start
bash scripts/achewiw-control.sh status
```

Пароли и SQLite живут только в `.env` и `data/`, исключённых из Git. `.env` **не отправлять в GitHub**, не выводить его содержимое в журналы. Если порт 7080 занят, скрипт откажется от запуска и **не убьёт чужой процесс**.

## Обновить из GitHub после установки

```bash
cd /1tbdisk/achewiw
bash scripts/achewiw-control.sh update
```

Скрипт проверяет, что текущая ветка — именно `feature/technical-epub-first`, а `origin` принадлежит `musikfed/achewiw`. Он отказывается обновлять грязный checkout или историю, которую нельзя быстро перемотать. Только после `git fetch` и `git merge --ff-only` он проверяет изменение Git SHA.

- Если `studio` был **запущен** и код обновился, Compose пересобирает **только** `studio` без зависимостей, остальные контейнеры и Ollama не трогает.
- Если `studio` был **остановлен**, после `update` он **останется остановленным**, а книги и SQLite сохранятся.
- Если новых коммитов нет, контейнер повторно не запускается и не пересобирается.
- При неудачной сборке Git-код уже мог обновиться. Скрипт сообщает об ошибке и не удаляет `data/`; восстановление предыдущей версии — отдельная проверенная оператором операция, **не автоматический hard reset**.
- Обновление не переключает ветки, не удаляет историю, не запускает Ollama, не управляет GPU и не очищает Docker images.

## Мониторинг, генерации и VRAM

Хостовые показатели CPU/RAM/RTX 3060 поступают из user-systemd сборщика в `data/monitor/host.json`. Когда `monitor-start` выполнен успешно, веб-карточки обновляются каждые 10 секунд. Если unit отсутствует, `start` попробует подключить только свой `ops/achewiw-host-monitor.service`, не создавая чужих служб и не вмешиваясь в Ollama. Для диагностики:

```bash
cd /1tbdisk/achewiw
bash scripts/achewiw-control.sh monitor-start
systemctl --user status achewiw-host-monitor.service --no-pager
journalctl --user -u achewiw-host-monitor.service -n 30 --no-pager
nvidia-smi
```

Сгенерированный текст сохраняется в **SQLite** в `data/achewiw.sqlite3`: проект и очередные задания находятся в таблицах `projects` и `ai_jobs`. После обновления вкладки задача продолжается на сервере, текст и примерные проценты восстанавливаются. При перезапуске контейнера задание прерывается, но записанные фрагменты остаются. PostgreSQL для этого не требуется.

Кнопка «Освободить VRAM модели» выгружает только подтверждённую выбранную модель общей хостовой Ollama и предупреждает о возможном влиянии на другие приложения, работающие с этой же моделью; не производит общий GPU reset.

[Полное руководство: мониторинг, прогресс, SQLite и безопасность VRAM](RUNTIME-GENERATION-GPU-SQLITE-RU.md).

## Почему на GitHub 7 веток

- `main` — основная ветка, **но текущий Compose-приложение живёт не в ней**.
- `feature/technical-epub-first` — активная рабочая ветка, где доступны `compose.yaml`, веб-приложение и эти команды.
- `feature/storyengine-mvp`, `feature/canon-storygraph`, `feature/publishing-pipeline-rfc`, `feature/release-gate-technical-books` содержат независимые коммиты, **не включённые целиком в main**.
- `feature/server-edits` полностью включена в `main` по графу коммитов, но её удаление не требуется для нормальной работы.

GitHub позволяет вести несколько направлений независимо, однако автоматическая очистка таких веток может потерять значимые изменения. **Ни одна ветка в ходе добавления скрипта не удалялась.** Перед упрощением истории следует отдельно перенести востребованные функции в одну основную ветку через PR, проверить сборку и только потом удалять архивные ветки.

## CI и диагностика

Автоматические тесты заменяют Docker/SSH/systemd/Git фиктивными командами и проверяют владение контейнерами, запрет запуска на занятом порту, отдельность проекта, отказ от опасного Git origin и fast-forward. GitHub CI не запускает Docker на Huananzhi — аппаратную работоспособность проверяет оператор.

Для диагностики без изменения проекта:

```bash
bash scripts/achewiw-control.sh status
docker compose ps
ss -ltn '( sport = :7080 )'
```

Никогда не отправляйте содержимое `.env`, приватные TLS-ключи или книги/SQLite в публичные issue.
