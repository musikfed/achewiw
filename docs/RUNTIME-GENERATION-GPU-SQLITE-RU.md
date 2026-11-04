# ACHEWIW · Huananzhi CORE · мониторинг, генерация, VRAM и SQLite

Рабочая ветка: **`feature/technical-epub-first`**. Сервер: Ubuntu 24.04, Huananzhi X79, 2×Xeon E5-2650, RTX 3060 12GB, 48GB RAM. Приложение находится в `/1tbdisk/achewiw`, адрес для доверенной LAN/VPN `http://192.168.0.100:7080`.

## Что находится в Docker и где данные

| Составляющая | Где работает / хранится |
| --- | --- |
| Веб-страница и API | Один контейнер Docker Compose `achewiw-studio`, Python 3.12 / FastAPI |
| Рукописи, проекты, предложения ИИ, история заданий | **SQLite**: `/1tbdisk/achewiw/data/achewiw.sqlite3` на хосте, `/data/achewiw.sqlite3` внутри контейнера |
| Прогресс и промежуточные тексты | В той же SQLite, таблица `ai_jobs`; запись по частям из Ollama |
| CPU, RAM, NVIDIA GPU/VRAM | Хостовый `achewiw-host-monitor.service` пишет `data/monitor/host.json`; контейнер только читает |
| Нейросеть, загруженные модели и GPU | **Ollama только на Ubuntu-хосте** (`127.0.0.1:11434` через Compose host networking), отдельно от Docker ACHEWIW |
| Пароль ACHEWIW и логин | Локальный `.env`, **не** в GitHub, не в SQLite |
| PostgreSQL | **Не используется и не требуется** в текущем одноавторском режиме |

Для SQLite предусмотрены таблицы `projects` и `ai_jobs`, индекс по проекту и единственный активный job. База располагается в примонтированной папке `./data` и **переживает пересборку контейнера, `restart` и `update`**. Если файл ещё отсутствует, создаётся при первом запуске приложения. Не удаляйте `data/` и не выполняйте `docker compose down -v`. Перед обновлением можно сделать резервную копию командой `sqlite3 data/achewiw.sqlite3 ".backup '/путь/к/резервной_копии.sqlite3'"` в безопасную локальную директорию, только если установлен `sqlite3`.

## 1. Установить исправления GitHub и перезапустить только ACHEWIW

```bash
cd /1tbdisk/achewiw
git branch --show-current
git status --short
bash scripts/achewiw-control.sh update
bash scripts/achewiw-control.sh status
```

`update` принимает только текущую ветку `feature/technical-epub-first`, подтверждённый `origin` и чистое рабочее дерево. Работавший контейнер пересобирается; остановленный останется остановленным. Если в checkout ещё старый управляющий скрипт, при чистой рабочей ветке сначала:

```bash
git fetch origin
git merge --ff-only origin/feature/technical-epub-first
bash scripts/achewiw-control.sh update
```

Не используйте `git reset --hard`, `git clean`, `docker system prune` или `sudo` для этих действий.

## 2. Данные о процессорах, RAM и RTX 3060

Показатели графического процессора **не появятся**, пока на ХОСТЕ не пишет показания отдельная пользовательская служба. В контейнере `nvidia-smi` не исполняется, графический драйвер не монтируется. Команда `start` теперь пробует безопасно включить собственный монитор, но может сообщить, что `systemd --user` не доступен.

```bash
cd /1tbdisk/achewiw
mkdir -p data/monitor
# Служба только из нашего проекта; проверяются точные пути unit и скрипта:
bash scripts/achewiw-control.sh monitor-start
bash scripts/achewiw-control.sh status
systemctl --user status achewiw-host-monitor.service --no-pager
# Отдельная диагностика GPU на Ubuntu:
nvidia-smi
# Один безопасный замер без фоновой службы (если systemd --user не работает):
python3 scripts/achewiw_host_monitor.py --once
```

Веб-панель обновляет показания раз в 10 секунд. Если датчик GPU не работает, будет «Нет данных», а не фиктивные нули. Веб-приложение не имеет доступа к Docker socket, к `nvidia-smi`, к чужим процессам, а также к `systemctl` хоста.

При ошибках: `journalctl --user -u achewiw-host-monitor.service -n 50 --no-pager`, `ls -ld data data/monitor`, `python3 scripts/achewiw_host_monitor.py --once`. Для исправления прав изменяйте только свои каталоги в `/1tbdisk/achewiw/data`.

## 3. Не терять генерацию при обновлении страницы

При нажатии «Сгенерировать каркас», «Предложить текст сцены» или «Литературный аудит» сервер сохраняет отдельное задание **в SQLite до начала ответа Ollama**. Вызов Ollama идёт асинхронно и потоково; поступающие части черновика регулярно сохраняются в таблицу `ai_jobs`. Каждые ~1.5 секунды веб-страница запрашивает актуальное состояние.

Отображаются: этап (очередь, ожидание Ollama, генерация, готово, ошибка), приблизительный процент **от максимального лимита ответа 2400 токенов**, ориентир времени до *лимита*, полученный текст и идентификатор задания. **Это не точная оценка завершения книги.** Модель может закончить значительно раньше, а время первой загрузки LLM не поддаётся надёжному ETA. Проценты не выдумываются до первых 40 символов.

Обновление браузера **не прерывает** выполняемую генерацию: после перезагрузки последнее задание проекта загружается из SQLite. Текст после завершения остаётся отдельным предложением, пока автор явно не нажмёт «Принять предложение». Если перезапустить **сам Docker-контейнер посреди генерации**, поток прервётся, задание получит `interrupted`, уже полученные части черновика останутся в SQLite. Это отличие обновления браузера от перезапуска сервера принципиально.

Защита от двух одновременных генераций реализована уникальным SQLite-индексом; активный Ollama-запрос может быть только один на экземпляр ACHEWIW.

## 4. Кнопка «Освободить VRAM модели»

Кнопка выгружает **только выбранную и реально загруженную модель** по официальному API Ollama `POST /api/generate` с `keep_alive: 0`. Она не запускает `nvidia-smi --gpu-reset`, не останавливает Ollama, не завершает GPU-процессы и не выгружает чужие модели автоматически.

**Внимание:** Ollama общая для всех программ Huananzhi. Если другой проект использует **эту же** модель, её выгрузка может повлиять и на него. Перед командой браузер требует осознанного подтверждения; при активной генерации ACHEWIW сервер отклоняет запрос. После команды посмотрите на карточку VRAM и `nvidia-smi`; освобождение памяти не гарантирует, что все видеопамять будет свободна.

## 5. Все команды жизненного цикла

```bash
cd /1tbdisk/achewiw

bash scripts/achewiw-control.sh start          # студия, попытка включить свой монитор
bash scripts/achewiw-control.sh status         # контейнер, порт, свежесть метрик
bash scripts/achewiw-control.sh monitor-start  # хостовые CPU / RAM / GPU (без GPU сброса)
bash scripts/achewiw-control.sh stop           # только контейнер и подтверждённый монитор
bash scripts/achewiw-control.sh restart        # только собственный контейнер/монитор
bash scripts/achewiw-control.sh update         # git fast-forward + rebuild работающего studio
```

Если понадобится посмотреть состояние SQLite без раскрытия текста:
```bash
cd /1tbdisk/achewiw
ls -lh data/achewiw.sqlite3
# При установленной утилите sqlite3:
sqlite3 data/achewiw.sqlite3 '.tables'
sqlite3 data/achewiw.sqlite3 'SELECT status, count(*) FROM ai_jobs GROUP BY status;'
```

**Не публикуйте** `.env`, `data/achewiw.sqlite3`, книги или промпты в GitHub. Развёртывание на физическом Huananzhi и наличие рабочего NVIDIA-драйвера GitHub CI проверить не может.

Документация управления: [ACHEWIW-CONTROL-RU.md](ACHEWIW-CONTROL-RU.md). Технический мониторинг: [MONITORING.md](MONITORING.md).
