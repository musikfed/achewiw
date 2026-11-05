# Продвижение ACHEWIW на GitHub и в поиске

## Название и описание

**Название:** ACHEWIW — Типография в кармане  
**Подзаголовок:** Закулисная мастерская рассказов, книг и выступлений.

**GitHub About → Description** (короткая версия для карточек и поисковой выдачи):

> Типография в кармане: локальная студия книг на Ollama. Сюжет, редактура, RAG, иллюстрации и выпуск PDF/DOCX/EPUB. Автор контролирует каждую правку.

**Английский вариант для международного поиска:**

> Self-hosted AI book studio with Ollama, RAG, FastAPI and SQLite. Write, review, illustrate and export books to EPUB, PDF, DOCX. Author-controlled.

**Website:** `https://github.com/musikfed/achewiw` (пока отдельного домена нет).

## Рекомендуемые GitHub topics

`ollama`, `self-hosted`, `ai-writing`, `book-publishing`,
`epub`, `pdf-generation`, `writing-tools`, `storytelling`,
`rag`, `fastapi`, `sqlite`, `comfyui`, `book-typesetting`,
`russian-language`, `document-generation`.

Вводятся на GitHub: **Repository → About → ⚙ Edit repository metadata → Topics**. 
GitHub Topics поддерживает до 20 тегов, строчные латинские буквы, цифры и дефис. Устанавливать только относящиеся к проекту; повторение или переизбыток тегов не гарантируют места в поиске.

## Иллюстрации и индексирование

- README в корне имеет русский основной текст и краткое English-описание.
- `docs/media/achewiw-home.svg` — вид главной страницы с пустой библиотекой.
- `docs/media/achewiw-editor-demo.svg` — пример редактора без настоящего автора.
- `docs/media/achewiw-conveyor-demo.svg` — редакционные роли.
- `docs/architecture/*.svg` — технические схемы, которые можно открыть и проверить.
- Для карточки в соцсетях использовать `docs/media/achewiw-social-preview.svg`; **репозитарий → Settings → Social preview → Upload an image** (может понадобиться PNG-преобразование, если интерфейс не принимает SVG).
- Первые 200 символов README содержат название и назначение; HTML-метатеги не используются вместо README — у GitHub собственный рендеринг.

## Как получить первые звёзды без накрутки

Опубликовать на странице GitHub три настоящих кейса: один короткий рассказ с указанием авторских правок, одну техническую главу со схемой, один сценарий выступления. Для каждого показать вход, последовательность этапов, результат и **проверенный EPUB/PDF**. Для ошибок открыть понятные GitHub Issues с минимальными воспроизводимыми примерами. Подготовить короткий видеопоказ главной страницы и локального переключения VRAM. В профильных сообществах делиться архитектурой и измерениями RTX 3060, а не обещать «автокниги без редактора». Не генерировать фиктивные звёзды, не рассылать массовый спам.

## Честные ограничения

- Полное соответствие ГОСТ Р 7.0.4-2020, корректный PDF/X, права и антиплагиат требуют отдельных проверок; не писать «сертифицировано», пока подтверждения нет.
- Автоматическая рецензия ИИ не равна профессиональной редактуре.
- Описание модели в README не гарантирует отсутствие OOM при произвольном `num_ctx` или одновременно занятой GPU.
- Индексация зависит от поисковых систем и GitHub, мгновенный топ выдачи и звёзды не гарантированы.

Справка: https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics

## Google и Яндекс: отдельная индексируемая страница

В `docs/index.html` добавлена **статическая публичная страница проекта**, не связанная с локальной SQLite, Ollama или рукописями. Там есть `title`, `description`, естественные русские тексты, заголовки, `alt` для схем, `canonical`, Open Graph и JSON-LD `SoftwareSourceCode`. На одной странице обсуждаются реальные сценарии использования и технические ограничения.

**Состояние на 5 ноября 2026:** GitHub Pages включён, источник `main /docs` (снимок настроек владельца; API `has_pages=true`). Это **не доказывает**, что поисковики уже проиндексировали страницу.

Настройка Pages уже выполнена владельцем. Теперь нужно проверить в браузере `https://musikfed.github.io/achewiw/` и статус HTTP 200. Это **только публичная документация**, не сам ACHEWIW Studio. Нельзя публиковать `/data`, бэкапы SQLite, токены, Ollama, порт 7080, приватные рукописи.

После успешного включения:

1. Проверить `title` и описание в открывшейся странице, наличие ссылок на `https://github.com/musikfed/achewiw` и корректное отображение изображений.
2. Добавить доступную URL-prefix собственность в [Google Search Console](https://search.google.com/search-console/about), проверить её доступным способом, отправить главную страницу через URL Inspection и отслеживать Page indexing. Верификация требует реального доступа к указанной странице/домену, не всегда доступна для чужого домена `github.io`.
3. Добавить доступный адрес в [Яндекс Вебмастер](https://webmaster.yandex.ru/), подтвердить права и проверить «Страницы в поиске», «Диагностика», «Статистика запросов».
4. Если проверку прав на подкаталог GitHub Pages выполнить нельзя, использовать собственный домен с доступом к DNS, настроить HTTPS и обновить canonical/OG/Schema.org на реальный адрес. Не выдавать чужой `github.io` за свой домен.
5. Связать страницу из профильной статьи о локальных LLM и авторской редактуре с конкретными техническими измерениями. Нужны реальные отзывы пользователей и качественные примеры, не дорвеи, спам и массовые дубли страниц.

Официальные руководства:

- [Google Search Essentials — доступность и индексирование](https://developers.google.com/search/docs/essentials/technical)
- [Google — полезный контент для людей](https://developers.google.com/search/docs/fundamentals/creating-helpful-content)
- [Яндекс — этапы обхода и индексирования](https://yandex.ru/support/webmaster/ru/yandex-indexing/site-indexing)
- [Яндекс — качество страниц, заголовки и описания](https://www.yandex.ru/support/webmaster/ru/site-quality-index)

**Важно:** README GitHub, его `About` и Topics редактируются отдельно от метатегов `docs/index.html`. GitHub в данный момент не получил описание и темы через подключение — для них нужно вручную воспользоваться иконкой редактирования About. Поисковые позиции, сроки и количество звёзд никто не гарантирует.

## Тематические метки и собственный домен: текущее состояние

- Поиск **Google не использует `meta keywords`**, поэтому нужны понятные `title`, `description`, обычный текст, внутренние ссылки и реальные материалы. **Яндекс допускает учёт `keywords`**; аккуратный русскоязычный метатег добавлен в обе лендинговые страницы.
- GitHub Topics и поле About остаются отдельными метаданными репозитория: API изменения требует `Administration: write`, а GitHub-подключение из диалога предоставляет только операции с файлами/ветками. GitHub Actions `contents: write` недостаточен для обновления этих полей. Не использовать небезопасные PAT в коммитах.
- Если на локальном ПК или сервере есть **авторизованный `gh` CLI**, можно настроить всё одной командной последовательностью без раскрытия ключа:

```bash
# Требуется gh auth login владельца с правом Administration:write
# Если gh не установлен/не авторизован, проще внести метаданные в About на GitHub.
gh api -X PATCH repos/musikfed/achewiw \
  -f description='Типография в кармане: локальная студия книг на Ollama — сюжет, редактура, RAG, иллюстрации, PDF/DOCX/EPUB' \
  -f homepage='https://musikfed.github.io/achewiw/'
gh api -X PUT repos/musikfed/achewiw/topics \
  -f 'names[]=ollama' -f 'names[]=self-hosted' -f 'names[]=ai-writing' \
  -f 'names[]=book-publishing' -f 'names[]=epub' \
  -f 'names[]=writing-tools' -f 'names[]=storytelling' \
  -f 'names[]=rag' -f 'names[]=fastapi' -f 'names[]=sqlite' \
  -f 'names[]=comfyui' -f 'names[]=russian-language'
```

- У владельца есть домен **`peginelab.ru`** (именно так он указан в конфигурации `musikfed/peginelab`, не `peginela.ru`). В `musikfed/peginelab` на ветке `main` добавлены `/achewiw/`, sitemap, Nginx-маршрут и карточка в каталоге проектов. **Production-домен не обновлён до разрешённого выпуска из preprod/prod**: нельзя подменять опубликованный сайт старой локальной сборкой.
- Страница на `peginelab.ru/achewiw/` должна быть самостоятельной индексируемой визиткой с ссылкой на **GitHub-репозиторий**, а не мгновенным 301-редиректом. Google может канонизировать 301 на GitHub, и тогда собственная тематическая страница не будет отдельным результатом поиска. Для быстрой навигации кнопка «Открыть ACHEWIW на GitHub» уже есть.

Документация: [Google meta keywords не поддерживаются](https://developers.google.com/search/docs/crawling-indexing/special-tags), [метатеги Яндекс](https://yandex.ru/support/webmaster/ru/controlling-robot/metatags), [GitHub API Topics требует Administration](https://docs.github.com/en/rest/repos/repos).
