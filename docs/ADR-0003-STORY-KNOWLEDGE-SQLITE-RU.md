# ADR-0003: единая SQLite для канона, RAG, графа причин и редакторского контроля

Статус: **принято как архитектурное направление, не как завершённая реализация**.
Дата: 2026-11-05.

## Решение

Оставить единую SQLite как authoritative source of truth ACHEWIW. Не добавлять PostgreSQL, Neo4j, Qdrant или отдельные контейнеры до измеренного ограничения масштаба, конкуренции писателей, векторного retrieval или графовых обходов. Изменения выполнять аддитивными versioned migrations с резервной копией; изоляция по `project_id`, `branch_id`, `canon_revision` и `job_id`.

**Важно:** RAG — метод извлечения, граф — модель точных отношений. Сами по себе они не являются интеллектуальным редактором. Нужна машина состояний персонажей/предметов/знаний, типизированные доказательства, ретривер и контроллер проверки каждого фрагмента.

## Существующая реализация

- `story_nodes`, `story_edges`, `story_branches`, `story_chunks`, `story_chunks_fts` в `app/story_memory.py`.
- `ai_book_chapters` — отдельные черновики с SQLite checkpoints.
- `projects.data` — исходные карточки/каркас в JSON; одобрение автором отдельное.
- `author_text_revisions` — ревизии уже принятого текста.
- Ollama работает на хосте, Studio в одном управляемом контейнере.
- FTS5 — лексическое извлечение. Нет встроенного embedding index. Отдельная graph DB не требуется для нынешних объёмов.

## Следующие таблицы / расширения — по фазам, не как безусловная автоматическая истина

### P1. Доказуемый канон и происхождение утверждений

`canon_revisions(id, project_id, branch_id, parent_revision_id, author_approved_at, created_at)`

`story_claims(id, project_id, branch_id, kind, subject_id, predicate, object_id, value_json, world_time_start, world_time_end, introduced_scene_id, status, confidence, source_revision_id, source_chunk_id)`

`story_claim_evidence(claim_id, scene_id, paragraph_id, text_span_start, text_span_end, evidence_kind, source_job_id)`

`story_proposals(id, project_id, job_id, scene_id, type, proposed_json, status, reviewed_at)`

**Правило:** LLM может создавать только `story_proposals`; канон меняется в одной транзакции только после авторского подтверждения. Типы статусов: `proposed`, `approved`, `rejected`, `superseded`, `conflicting`. Для доказательств фиксировать hash исходного текста и версии, иначе при правке страницы offsets устареют.

### P2. История как переходы состояний

`story_events(id, project_id, branch_id, event_time_start, event_time_end, narration_order, scene_id, cause_event_id, status)`

`story_state_changes(id, event_id, entity_id, attribute, before_json, after_json, precondition_json)`

`story_knowledge(id, project_id, branch_id, character_id, claim_id, learned_event_id, belief_status)`

`story_mystery_tracks(id, project_id, branch_id, question_claim_id, setup_event_id, payoff_event_id, scheduled_reveal_scene_id, status)`

**Разделить:**
1. время в мире истории;
2. порядок подачи читателю;
3. знания персонажа в момент события;
4. знания читателя к моменту страницы.

Например: герой не может знать тайну, которая раскрывается читателю позже, если в каноне не указан независимый способ узнать её.

### P3. Поиск и контроль продолжения

- FTS5 остаётся первичным retrieval, факты для запросов выбираются SQL по ID и временным интервалам; граф обходится через recursive CTE с ограничением глубины.
- Не полагаться на векторную близость при проверке истинности: она возвращает кандидатов, а не доказательства.
- При необходимости добавить отдельную необязательную embedding table `story_embeddings(chunk_id, model_id, dimension, vector_blob, source_digest)`, использовать host Ollama `nomic-embed-text`. На старте допустим Python cosine ranking для небольшой книги, позже `sqlite-vec` если доступен в образе и измеренно нужен.
- Сборка контекста: `canon_revision` + главная задача + разрешённые знания + граф prerequisites + точные факты + релевантные snippets + последний контекст + запреты на раскрытие.
- После выдачи chunk LLM предлагает структурированные `story_claims` и изменения состояний **как proposals**; детерминированный валидатор проверяет время/предусловия/ссылки и происхождение; LLM-рецензент создаёт гипотезы, не бесшумно меняя утверждённое.
- При коллизии: остановить принятие проблемного chunk либо пометить сомнение и предложить автору исправление; не изменять завершённые главы за спиной автора.

### P4. Ветвление

Copy-on-write branch overlay: дочерняя ветка читает зафиксированный immutable snapshot родителя до `fork_event_id`, а после него — собственные events, claims, states и text revisions. Уникальные stable IDs; запрещено утекание будущих событий из main или соседних branches. Нужны тесты inheritance / fork isolation / rewind / stale canon.

## Индексы и транзакции

- Уникальные/внешние ключи на `(project_id,branch_id,entity_id)` (составные там, где нужны), `PRAGMA foreign_keys=ON`; индексы для времени, статусов и существующих `scene_index`/job scope.
- WAL и `busy_timeout` — только после проверки совместимости с томом/бэкапом. Один сериализованный авторский writer; read snapshots для retrieval.
- Транзакция: принять chunk (или proposal) + evidence + проверка ревизии. Не выполнять сетевые Ollama вызовы внутри открытой транзакции.
- Фоновые embeddings и FTS — воспроизводимые индексы по `digest`, не единственный источник факта.
- Версионированные миграции с dry-run, `integrity_check`, резервной копией и rollback procedure, без удаления рукописей.

## Почему пока не PostgreSQL и не несколько БД

Для локальной книги десятки тысяч слов и даже коллекции романов не требуют отдельного graph server. Переход на PostgreSQL с pgvector имеет смысл при нескольких одновременных авторах, длительных writer-lock, запросах по миллионам фрагментов либо требовании серверного разграничения доступа. Neo4j/Qdrant — только после подтверждённых графовых/vector bottlenecks. С несколькими базами появляются cross-DB consistency, outbox/reconciliation, replay и дополнительные точки отказа.

## Критерии приёмки интеллектуального редактора

1. «Ключ потерян в главе 3 → не может быть в кармане в главе 4 без перехода владения».
2. «Персонаж не знает имя похитителя до соответствующего события».
3. «Читатель может получить намёк, не получая полного раскрытия».
4. «Флешбэк меняет порядок повествования, но не временную причинность».
5. «Предложенная LLM правка не меняет канон без подтверждения автора».
6. «При переписывании абзаца доказательства пересчитываются/помечаются устаревшими».
7. «Ветка what-if не загрязняет main, сохраняет родительскую историю до fork».
8. «Остановленная книга возобновляется с прежнего checkpoint, а не с нуля».
9. «У каждого QA-замечания ID события/факта/фрагмента и уровень уверенности».
10. «Проверки не обещают литературного качества: неоднозначность и авторские художественные исключения допускаются».

**Граница текущего релиза:** этот ADR описывает требуемый следующий слой, но не утверждает, что новые таблицы, временная модель и state-check уже реализованы.
