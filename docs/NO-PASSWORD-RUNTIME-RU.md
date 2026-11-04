# Вход без пароля: восстановить веб-интерфейс ACHEWIW на 7080

**Рабочая версия:** ветка `feature/technical-epub-first`, `/1tbdisk/achewiw` на Ubuntu, http://192.168.0.100:7080.

## Почему браузер может ещё спрашивать логин и пароль

Окно «Войти», предлагающее пользователя `author`, **не является HTML-формой**. Браузер получил HTTP `401 Unauthorized` и заголовок `WWW-Authenticate: Basic` от какого-то процесса на порту 7080. В исходниках актуальной версии `app/main.py` **нет FastAPI HTTPBasic**. В `compose.yaml` уже нет обязательной переменной `ACHEWIW_PASSWORD`. Следовательно, старый запрос аутентификации может исходить из:

1. не пересобранного старого Docker-образа (`docker restart` **не заменяет** образ);
2. другого серверного процесса или прокси, обслуживающего тот же IP/порт;
3. старых данных о попытках входа в браузере (проверить в приватном окне **после** подтверждения HTTP).

**Не удалять `.env` и базу для исправления этой ошибки.** Не чистить чужие контейнеры, не использовать `killall` или `docker system prune`.

## Обновить строго ACHEWIW и проверить отсутствие HTTP Basic

```bash
bash <<'BASH'
set -euo pipefail
cd /1tbdisk/achewiw

[[ "$(git branch --show-current)" == "feature/technical-epub-first" ]] || {
  echo "ОСТАНОВЛЕНО: другая Git-ветка"; exit 1;
}
case "$(git remote get-url origin)" in
  https://github.com/musikfed/achewiw|https://github.com/musikfed/achewiw.git|git@github.com:musikfed/achewiw|git@github.com:musikfed/achewiw.git) ;;
  *) echo "ОСТАНОВЛЕНО: чужой Git origin"; exit 1;;
esac
[[ -z "$(git status --porcelain)" ]] || {
  git status --short
  echo "ОСТАНОВЛЕНО: есть локальные изменения, ничего не затираем"; exit 1;
}

git fetch --no-tags origin
git merge --ff-only origin/feature/technical-epub-first
bash -n scripts/achewiw-control.sh

# Только собственный контейнер ACHEWIW с полным набором Docker-меток;
# ни host Ollama, ни соседние Docker-проекты не затрагиваются.
bash scripts/achewiw-control.sh rebuild

# Подтверждает HTTP 200, X-ACHEWIW-App и отсутствие Basic.
bash scripts/achewiw-control.sh auth-check
bash scripts/achewiw-control.sh status
echo "ГОТОВО: новая ACHEWIW без пароля; data/ и .env сохранены."
BASH
```

`rebuild` выполняет `docker compose ... up -d --build --force-recreate --no-deps studio` в каталоге `/1tbdisk/achewiw` **только после строгой проверки меток**. Если обнаружен чужой или старый неподтверждённый контейнер, команда откажется от изменений. Если ACHEWIW был остановлен, команда не включает его без отдельного `start`.

## Проверить сервер при ошибке

```bash
cd /1tbdisk/achewiw
git status -sb
git log -1 --oneline
docker compose -f compose.yaml ps
ss -ltnp '( sport = :7080 )'
bash scripts/achewiw-control.sh auth-check
```

Диагностика делает **только чтение**, не меняет конфигурацию и не показывает пароли из `.env`.

`auth-check` проверяет оба локальных адреса `127.0.0.1:7080` и `192.168.0.100:7080`, передаёт Host `192.168.0.100:7080`, не передаёт имя пользователя/пароль и требует:
- HTTP `200 OK`;
- заголовки `X-ACHEWIW-App: studio` и `X-ACHEWIW-Auth: private-vpn-no-password`;
- отсутствие `WWW-Authenticate: Basic`.

Если вывод содержит 401 — это **по-прежнему старый процесс или прокси**. Если вывод говорит об отсутствии заголовков ACHEWIW — вероятнее другой сервер. Не пытаться глушить неизвестный PID: пришли вывод диагностики.

После успешной проверки можно открыть `http://192.168.0.100:7080` в **новом приватном окне**, чтобы исключить сохранённый браузером Basic-запрос.

## Какие ограничения остаются

Вход не требует пароля, но код FastAPI допускает только приватные IPv4/IPv6 адреса клиентов, проверяет разрешённое значение Host (по умолчанию `192.168.0.100`, `127.0.0.1`, `localhost`) и происхождение браузерных запросов изменения данных. **Не публиковать порт 7080 в Интернет.**

**Каждый участник доверенной VPN/LAN теперь потенциально может читать, менять и удалять рукописи.** Это сознательный режим личной мастерской, не система многопользовательской аутентификации. Сеть должна оставаться доверенной. Ссылки на внешние сервисы/обратные прокси требуют отдельного решения безопасности.

Ни один GitHub-коммит не обновляет физический сервер автоматически. Отдельные команды в Ubuntu выполняет владелец сервера, GitHub-коннектор не имеет доступа к приватному VPN-адресу.
