# ACHEWIW — Закулисная мастерская рассказов, книг и выступлений

**Название продукта:** «Закулисная мастерская» — закулисное пространство автора для построения канона, томов, глав, сцен и абзацев. Рассказ, книга, выступление, сценарий и другие повествовательные формы.

**Каталог проекта на Ubuntu:** `/1tbdisk/achewiw` — **вне** `/1tbdisk/PegineLab/projects`. Остальные проекты (`wificam_T23N`, `YadroKreator`) не затрагивать. [Инструкция GitHub ↔ сервер и безопасный скрипт переноса](https://github.com/musikfed/achewiw/blob/main/docs/GIT-SERVER-SYNC.md). Перенос не запускает Docker или Ollama.

**A cherished word is a wondrous treasure.** «Слово чтимое — ценность дивная».

Самостоятельная русскоязычная студия для проектирования фантастики, рассказов, романов, сценариев и выступлений.

## Строгое разделение компонентов

- **Ollama и все LLM уже работают на хосте Ubuntu** — ничего не устанавливаем в контейнер и не скачиваем модели.
- Docker запускает **только** веб-сервер ACHEWIW (FastAPI, HTML/CSS/JS) и SQLite в volume-папке.
- В Linux-сборке используется `network_mode: host`. Контейнер подключается к хостовой Ollama через `http://127.0.0.1:11434`.
- ACHEWIW слушает `0.0.0.0:7080`, снаружи откроется по VPN: `http://192.168.0.100:7080`. Ollama не публикуется на дополнительном внешнем порту.
- **RTX 3060 не передаётся контейнеру**; GPU управляется существующей службой Ollama на Ubuntu.

## Запуск на Ubuntu 24.04

```bash
cd /1tbdisk
git clone https://github.com/musikfed/achewiw.git
cd achewiw
git checkout feature/storyengine-mvp
cp .env.example .env
# Откройте .env и задайте непустой длинный случайный ACHEWIW_PASSWORD
nano .env
mkdir -p data
# Если ваш uid/gid не 1000: поменяйте ACHEWIW_UID/GID в .env
sudo chown "$(id -u):$(id -g)" data
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:7080/api/health
```

Откройте **http://192.168.0.100:7080** по вашему VPN, в HTTP Basic Auth укажите `ACHEWIW_USERNAME` (по умолчанию `author`) и пароль из `.env`.

Проверьте Ollama **на самом хосте**:

```bash
systemctl status ollama
curl -s http://127.0.0.1:11434/api/tags
ollama list
```

Если Ollama уже слушает localhost, менять её настройки **не нужно**. Если порт 7080 занят, найдите сервис командой `sudo ss -lntp | grep :7080` (в host-networking параметр `ports` не используется).

Если Ubuntu Firewall разрешает VPN-подсеть, нужен доступ по TCP 7080 только через VPN. **Не открывайте 7080 в публичный интернет:** в MVP HTTP Basic Auth без TLS, поэтому используй VPN, который шифрует транспорт. Существующие сертификаты `~/crt-cert` приложению не нужны; в репозиторий их **не добавлять**.

Логи: `docker compose logs -f --tail=100`. Остановка: `docker compose down` (папка `./data` останется на диске). Резервная копия: `cp data/achewiw.sqlite3 backup.sqlite3` при остановленном контейнере (или SQLite backup API при работающем).

## Возможности v0.1

1. ТЗ: жанр, атмосфера, аудитория, время/объём, чтение или речь, твист, момент раскрытия, якоря.
2. Story.prj: карточки сцен, 5 активных сущностей, темп, подсказки и крючки. Генерация структуры локальной Ollama (JSON).
3. Редактор сцены: отдельное предложение ИИ, **ручное принятие и сохранение**, без фоновой перезаписи.
4. QA: слова, минуты чтения вслух (150 слов/мин), предупреждения по перегрузу, интеллектуальный аудит.
5. SQLite-сохранение, экспорт в Markdown, модели Ollama из локального списка.

**Данные** хранятся только в `./data`; `.env` и `data/` исключены из Git. Web UI — без внешних CDN, JavaScript или облачных AI-вызовов. Продукт рассчитан на одного доверенного автора, а не на многопользовательский публичный SaaS.

## Тестирование (не требуется для сервера)

```bash
cd app
python -m pip install -r requirements.txt pytest
PYTHONPATH=. python -m pytest ../tests -q
```

## Планы

Версионирование сцен, Git-ветви сюжетов, граф хронологии, экспорт DOCX/PDF, streaming генерации, сравнение редакций и HTTPS reverse proxy.
