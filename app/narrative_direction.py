"""Fiction-specific editorial direction, not a replacement for the author's canon.

This module does not perform automatic plot invention or alter saved text.
It gives each Ollama call a verifiable scene objective and flags extreme
low-information repetition for author review.
"""
from __future__ import annotations

import re
from collections import Counter

from prose_quality import _tokens

VAGUE_LINES = (
    r"\b(?:я|мы)\s+не\s+зна(?:ю|ем)\b",
    r"\b(?:я|мы)\s+чувствую(?:ем)?\b",
    r"\bэто\s+(?:только\s+)?начало\b",
    r"\bэто\s+не\s+конец\b",
    r"\b(?:мы|нам)\s+(?:должны|должен|должна|нужно)\s+продолж",
    r"\bэто\s+наш\s+путь\b",
    r"\b(?:мы|они)\s+(?:уже\s+)?в\s+этом\s+мире\b",
    r"\bчто-то\s+(?:важное|непонятное|странное)\b",
)

def _section_phase(existing_words: int, chapter_goal: int) -> str:
    share=existing_words/max(1,chapter_goal)
    return "вход" if share<0.18 else "усложнение" if share<0.60 else "последствие" if share<0.88 else "переход_к_финалу"


def scene_contract(project, scene, chapter_index: int, chapter_plan: dict,
                   existing_words: int, chapter_target: int) -> str:
    """Specific next-episode obligations; only author-supplied facts are used."""
    chapter_plan=chapter_plan or {}
    phase=_section_phase(existing_words,chapter_target)
    stage=chapter_plan.get("stage_name","development")
    aims={
       "opening":"Покажи героя через наблюдаемое действие; назови понятную локальную проблему, задай читателю конкретный вопрос.",
       "development":"Исследуй возникший конфликт: герой предпринимает попытку, сталкивается с сопротивлением, получает новую информацию.",
       "conflict":"Пусть герою помешает причина из уже известного мира; выбор меняет риск, доступ к предмету или союз с другим героем.",
       "reveal":"Свяжи новую улику с ранее показанной подробностью, не создавай объяснение без подготовки.",
       "climax":"Опирайся на подготовленные причины; герой принимает необратимое решение, а последствия наблюдаемы.",
       "resolution":"Заверши обещанные линии действиями, покажи последствия сделанного выбора без новой непредвиденной тайны.",
    }
    phase_goal={
       "вход":"Покажи, где герои находятся и что произошло непосредственно перед сценой. Установи их конкретную ближайшую цель.",
       "усложнение":"Добавь препятствие, проверку или конфликт. Каждый поступок имеет понятную причину.",
       "последствие":"Покажи изменение знания, риска, отношений или положения героя ПОСЛЕ его действия, не повторяй обсуждение.",
       "переход_к_финалу":"Подготовь следующий поворот или завершение локальной задачи; не перескакивай к развязке всей книги.",
    }
    cast=", ".join(x.strip() for x in scene.active_objects if x.strip())[:400]
    reference=(scene.anchor or scene.text or scene.hook or "").strip()[:500]
    return (
        "\nДРАМАТУРГИЧЕСКИЙ КОНТРАКТ СЛЕДУЮЩЕГО ФРАГМЕНТА — обязателен:\n"
        f"Этап главы: {phase}. Роль главы: {stage}. Цель: {aims.get(stage,aims['development'])}\n"
        f"Непосредственный шаг: {phase_goal[phase]}\n"
        f"Авторский сюжетный ориентир: {reference or 'Только уже заданный автором каркас, без самостоятельного изменения канона.'}\n"
        f"Участники по карточке автора: {cast or 'Не вводить лишних персонажей без необходимости.'}\n"
        "Введи нового героя при первом появлении понятным читателю образом: кто он в отношении "
        "остальных, зачем здесь и что сейчас делает. Не перечисляй персонажей без роли.\n"
        "Каждые несколько абзацев должны показывать наблюдаемое изменение: действие → "
        "препятствие → проверяемое последствие. Не выдумывай сюжетные факты из цитат RAG.\n"
        "Диалог: каждому говорящему нужен конкретный предмет разговора, противоположная "
        "цель или уточнение факта. Нельзя заполнять сцену репликами «это начало», "
        "«мы должны продолжать», «я чувствую», «это наш путь» и их перестановками. "
        "Показывай, что именно проверили, поняли или решили, а не бесконечную готовность действовать.\n"
        "Пиши разнообразными связными абзацами с реальными наблюдениями и действиями; "
        "обычный повествовательный абзац ориентировочно 45–110 слов, но короткие "
        "реплики и ритмические паузы допустимы. Не дроби каждое предложение в отдельный абзац. "
        "Не раздувай описания неопределёнными «что-то», «какой-то», «как будто».\n"
        "Критерий приёмки: после фрагмента читатель должен назвать новое событие, "
        "изменившее ситуацию, и понять, почему оно произошло. Если событие не произошло, "
        "вместо рассуждений напиши конкретное действие из плана."
    )


def fiction_warnings(text: str) -> list[dict]:
    """Conservative lexical editorial signals, not a semantic truth oracle."""
    if not text.strip():return []
    words,_=_tokens(text)
    if len(words)<110:return []
    paragraphs=[p.strip() for p in re.split(r"\n\s*\n",text) if p.strip()]
    alerts=[]
    dialogue=[line.strip() for line in text.splitlines()
              if line.strip().startswith(("—","–","-"))]
    if len(dialogue)>=8:
        patterns=[re.compile(p,re.IGNORECASE) for p in VAGUE_LINES]
        stale=sum(bool(any(p.search(line) for p in patterns)) for line in dialogue)
        if stale>=5 and stale/len(dialogue)>=0.40:
            alerts.append({"kind":"empty_dialogue",
                 "severity":"warning",
                 "detail":f"{stale} из {len(dialogue)} реплик повторяют неопределённые обещания/чувства вместо действий"})
    if len(paragraphs)>=14:
        tiny=sum(1 for p in paragraphs if len(_tokens(p)[0])<17)
        if tiny/len(paragraphs)>0.72:
            alerts.append({"kind":"fragmented_paragraphs",
                 "severity":"review",
                 "detail":f"{tiny} из {len(paragraphs)} абзацев короче 17 слов; проверь, не дробится ли проза"})
    abstractions=[w for w in words if w in ("чувствовал","чувствовала","чувствовали",
                  "неизвестность","что-то","нечто","как-будто")]
    if len(abstractions)>=max(14,int(len(words)*0.065)):
        alerts.append({"kind":"vague_scene",
              "severity":"review",
              "detail":"Слишком много неопределённых переживаний и слишком мало наблюдаемого действия"})
    return alerts


def reject_vacant_dialogue(text: str) -> None:
    """Auto-retry only the extreme exact failure pattern from the user's book.

    Ambiguous literary judgments remain review-only. No false claim that the
    validator can logically understand the story.
    """
    for problem in fiction_warnings(text):
        if problem["kind"]=="empty_dialogue":
            raise ValueError("Пустой диалог, повторяется обсуждение без действия: "+problem["detail"])
