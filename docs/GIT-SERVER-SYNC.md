# ACHEWIW — GitHub ↔ Ubuntu: перенос и синхронизация (без запуска)

**Проект:** https://github.com/musikfed/achewiw  
**Единственное штатное расположение на сервере:** `/1tbdisk/achewiw`  
**Прежнее расположение (только для переноса):** `/1tbdisk/PegineLab/projects/achewiw`

`/1tbdisk/PegineLab/projects` содержит независимые проекты `YadroKreator` и `wificam_T23N`; **не очищайте и не удаляйте** ни этот каталог, ни соседние проекты. Все обычные операции Git для ACHEWIW выполняйте только в `/1tbdisk/achewiw`.

Команды ниже **не запускают** Docker, приложение, Ollama или LLM. Порт `7080` относится к будущему веб-приложению, но перенос исходников сам по себе ничего не открывает и не запускает.

## 0. Один раз: перенести ACHEWIW из PegineLab/projects в /1tbdisk

[Посмотреть проверенный на временных папках скрипт переноса](../scripts/move-achewiw-to-root.sh). Скрипт предназначен для Ubuntu 24.04 и строго ограничивает действия двумя путями ACHEWIW. Чтобы выполнить его **со старого расположения**, скачайте исходник скрипта отдельно, проверьте и запустите:

```bash
cd /1tbdisk
curl -fL --retry 2 
  https://raw.githubusercontent.com/musikfed/achewiw/main/scripts/move-achewiw-to-root.sh 
  -o move-achewiw-to-root.sh
bash -n move-achewiw-to-root.sh
less move-achewiw-to-root.sh
bash move-achewiw-to-root.sh
```

Сценарии:

| Состояние на диске | Действие |
| --- | --- |
| Есть старая `/1tbdisk/PegineLab/projects/achewiw`, новой нет | **Переместить** старую папку целиком в `/1tbdisk/achewiw` (после `mv` старого пути больше нет). Файлы `.git`, `.env`, `data/` и незакоммиченные правки сохраняются. |
| Старая и новая существуют | **Не сливать и не удалять данные:** новую оставить нетронутой, старую переместить в `/1tbdisk/achewiw.previous.ДАТА-PID`. Исходная папка исчезает из `PegineLab/projects`, копия сохранена для ручной сверки. |
| Только новая существует | Ничего не менять, вывести статус. |
| Ни одна не существует | Клонировать `feature/technical-epub-first` прямо в `/1tbdisk/achewiw` (ветка разработки с кодом MVP и RFC, а не готовый релиз). |
| Старый путь является символической ссылкой | Прервать операцию и не переходить по ссылке. |
| Контейнер `achewiw-studio` обнаружен работающим | Прервать операцию; автоматической остановки/перезапуска не делать. |

Это **перенос папки, не необратимое удаление**. `rm -rf`, `git reset --hard`, `git clean -fdx` не используются. Если есть папки `achewiw.backup.*` от старых инструкций, они отдельно **не удаляются**; сначала проверьте данные, при необходимости перенесите их вручную. При переносе уже работающего проекта надо отдельно проверить внешние абсолютные пути и конфигурации — скрипт их не редактирует.

Убедиться в результате:

```bash
ls -ld /1tbdisk/achewiw
test ! -e /1tbdisk/PegineLab/projects/achewiw && echo "Старой папки нет"
git -C /1tbdisk/achewiw status -sb
ls /1tbdisk/PegineLab/projects  # другие проекты остаются
```

## 1. GitHub → сервер: получить / обновить код

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

## Когда будем запускать

Развёртывание и скрипт запуска — **позже и отдельно**, из `/1tbdisk/achewiw`. Оллама остаётся на Ubuntu-хосте, веб-студия позднее слушает 7080; правило UFW уже существует (`Skipping adding existing rule` не ошибка). Сейчас **только перенос/исходники**.
