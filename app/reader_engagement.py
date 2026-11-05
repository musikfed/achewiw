"""Reader-engagement contracts from author-authored facts, not invented lore.

Prompts are obligations. Only the literary critic and author may evaluate
whether a clue or revelation is actually present in saved prose.
"""
from __future__ import annotations

import re

STAGES = {
    "opening": ("обещание", "Герой действует, читатель понимает его цель, риск и конкретный вопрос."),
    "development": ("проверка", "Герой проверяет гипотезу, новая улика меняет план."),
    "conflict": ("эскалация", "Сопротивление увеличивает цену ошибки, гипотеза проверяется."),
    "reveal": ("переоценка", "Ранние улики получают новую причинную связь."),
    "climax": ("расплата", "Подготовленная разгадка приводит к необратимому выбору."),
    "resolution": ("последствие", "Закрой обещания и покажи последствия без новой внезапной тайны."),
}
DISCLOSURE = {
    "none": "Не раскрывай главный секрет.",
    "hint": "Покажи проверяемую улику, не объясняй окончательный ответ.",
    "partial": "Раскрой лишь проверенную промежуточную причину; оставь центральный секрет.",
    "full": "Допустима запланированная автором разгадка, опирающаяся на прежние улики.",
    "aftermath": "Ответ раскрыт ранее: покажи последствия, а не новую загадку.",
}
MODES = {
    "detective": ("улика → гипотеза → проверка → подтверждение/опровержение",
                  "Разгадка должна быть честной и объяснимой по ранее показанным уликам."),
    "serial": ("локальная задача эпизода → результат → большая незавершённая линия",
               "Закрывай хотя бы одну локальную задачу каждой главы."),
    "literary": ("желание героя → сопротивление → решение → последствие",
                 "Интрига рождается из характера и выбора, не из случайного шока."),
}


def choose_mode(genre: str) -> str:
    name=(genre or "").casefold()
    if any(x in name for x in ("детектив","расслед","триллер","мистер","mystery","detective")):
        return "detective"
    if any(x in name for x in ("сериал","эпизод","цикл","series","serial")):
        return "serial"
    return "literary"


def trim(value, limit: int=300) -> str:
    return " ".join(str(value or "").split())[:limit]


def map_chapters(project, plan: dict) -> dict:
    """Read-only map of author promises, not evidence of completed prose."""
    if getattr(project,"work_type","fiction")!="fiction":
        return {"enabled":False,"reason":"Для нехудожественных разделов воронка не навязывается.","chapters":[]}
    scenes=list(project.scenes)
    cards=plan.get("chapters",[])
    if len(scenes)!=len(cards) or any(s.id!=c.get("scene_id") for s,c in zip(scenes,cards)):
        raise ValueError("Режиссура глав не совпадает с авторским каркасом")
    mode=choose_mode(getattr(project,"genre",""))
    chapters=[]
    for i,(scene,card) in enumerate(zip(scenes,cards),1):
        stage=card.get("stage","development")
        reveal=card.get("reveal_level","none")
        if stage not in STAGES or reveal not in DISCLOSURE:
            raise ValueError("Неизвестная роль главы или ступень разгадки")
        chapters.append({
            "index":i,"scene_id":scene.id,"title":scene.title,
            "stage":stage,"tension":card.get("tension",3),
            "reveal_level":reveal,"beat":STAGES[stage][0],
            "reader_question":trim(getattr(scene,"hook",""),300),
            "author_clue_or_event":trim(getattr(scene,"anchor",""),400),
            "narrative_obligation":STAGES[stage][1],
            "disclosure_guard":DISCLOSURE[reveal],
            "release_answer":reveal in ("full","aftermath"),
            "author_goal":trim(card.get("editorial_goal",""),350),
            "has_authored_hook":bool(trim(getattr(scene,"hook",""))),
            "has_authored_anchor":bool(trim(getattr(scene,"anchor",""))),
        })
    return {"enabled":True,"mode":mode,"technique":MODES[mode][0],
            "safeguard":MODES[mode][1],
            "has_central_question":bool(trim(getattr(project,"twist",""))),
            "has_authored_resolution":bool(trim(getattr(project,"reveal",""))),
            "chapters":chapters,
            "warning":"Карта задаёт проверяемые обещания, но не доказывает их исполнение в тексте. " 
                      "Не добавляет новые факты в канон."}


def fragment_contract(project,scene,chapter_index:int,chapter_plan:dict,
                      existing_words:int,chapter_target:int) -> str:
    """Bounded, genre-aware next-chunk direction with fair-play disclosure."""
    if getattr(project,"work_type","fiction")!="fiction":
        return ""
    stage=chapter_plan.get("stage_name","development")
    reveal=chapter_plan.get("reveal_level","none")
    if stage not in STAGES or reveal not in DISCLOSURE:
        return ""
    mode=choose_mode(getattr(project,"genre",""))
    progress=existing_words/max(1,chapter_target)
    step=("Поставь понятную ближайшую цель и покажи действие." if progress<.18 else
          "Проверь гипотезу действием, получи новый факт или сопротивление." if progress<.62 else
          "Покажи изменение знания, отношений и цены выбора после проверки." if progress<.88 else
          "Закрой локальную задачу главы и подготовь честный следующий поворот.")
    return (
        "\nВОРОНКА ВНИМАНИЯ — ТОЛЬКО УТВЕРЖДЁННЫЙ АВТОРСКИЙ СЮЖЕТ:\n"
        f"Жанровый приём: {MODES[mode][0]}. {MODES[mode][1]}\n"
        f"Функция главы: {STAGES[stage][0]}. {STAGES[stage][1]}\n"
        f"Разрешённая степень раскрытия: {DISCLOSURE[reveal]}\n"
        f"Шаг данного фрагмента: {step}\n"
        f"Авторская интрига главы: {trim(getattr(scene,'hook',''),220) or 'Не выдумывать новую главную тайну.'}\n"
        f"Основание действия: {trim(getattr(scene,'anchor',''),270) or 'Применяй лишь уже известные события.'}\n"
        "Читатель должен получить: цель → наблюдаемую попытку → улику или следствие → "
        "следующее обоснованное решение. Ложный след допустим лишь при проверяемых основаниях "
        "и последующем честном опровержении. Не имитируй конкретные сериалы или книги, "
        "не подменяй сюжет повторяющимися вопросами. "
        "Не делай клиффхэнгер в конце каждого куска генерации. "
        "Если тайна автором не задумана, не придумывай её ради удержания. "
        "Пиши цельную художественную прозу со связными абзацами, а не сценарные реплики.\n"
    )


def review_contract(project,scene,chapter_plan:dict) -> str:
    """Evidence-only independent critique, not a literary score."""
    if getattr(project,"work_type","fiction")!="fiction":
        return ""
    stage=chapter_plan.get("stage_name",chapter_plan.get("stage","development"))
    reveal=chapter_plan.get("reveal_level","none")
    return (
        "\nАУДИТ ВНИМАНИЯ ЧИТАТЕЛЯ. По цитатам именно этого фрагмента найди "
        "(1) цель героя, (2) попытку и препятствие, (3) новую улику или знание, "
        "(4) последствие, (5) открытый вопрос, (6) подготовку или честную разгадку. "
        "Если доказательств нет, пиши «не найдено», не сочиняй факты. "
        "Отмечай пустые разговоры, ложные сенсации и не отвеченные до конца локальные вопросы. "
        f"Плановая роль: {STAGES.get(stage,STAGES['development'])[0]}. "
        f"Ограничение раскрытия: {DISCLOSURE.get(reveal,DISCLOSURE['none'])} "
        "Нельзя считать отсутствие ответа ошибкой до запланированной главы, "
        "но нельзя объявлять разгадку подтверждённой без текста, который её доказывает.\n"
    )


def lexical_findings(text: str) -> list[dict]:
    """Very conservative warnings; no fiction-quality claims from regex alone."""
    words=re.findall(r"[а-яёa-z]+",text.casefold())
    if len(words)<200:
        return []
    issues=[]
    q=text.count("?")
    if q>=14 and q/max(1,len(words))>.023:
        issues.append({"kind":"question_spam","severity":"review",
                       "detail":"Много вопросов: проверь наличие проверяемых действий и ответов."})
    tail=text.rstrip()[-600:].casefold()
    if any(x in tail for x in ("это только начало","это ещё не конец",
                               "они ещё не знали","продолжение следует")):
        issues.append({"kind":"generic_cliffhanger","severity":"review",
                       "detail":"В конце возможен штамп-клиффхэнгер вместо конкретного события."})
    return issues
