"""Edition typography helpers. No manuscript mutation and no guessed author/year."""
from __future__ import annotations
import re
from datetime import datetime

APP_VERSION = "0.6.0"
APP_NAME = "ACHEWIW StoryEngine"
COVER_SCENE_ID = "0" * 32
APP_CREDIT = (
    "Создано в ACHEWIW StoryEngine v" + APP_VERSION +
    " (Python, FastAPI, SQLite; Ollama для текста и ComfyUI/SDXL "
    "для изображений — при использовании)."
)

def info(project) -> dict:
    author=str(getattr(project,"author_name","") or "").strip()
    publisher=str(getattr(project,"publisher_name","") or "").strip()
    edition=str(getattr(project,"edition_label","") or "").strip()
    raw=getattr(project,"publication_year",None)
    year=int(raw) if raw not in (None,"") else None
    if year is not None and not 1900<=year<=2200:
        raise ValueError("Год выпуска должен быть от 1900 до 2200")
    return dict(title=str(project.title),author=author,publisher=publisher,
                edition=edition,year=year,credit=APP_CREDIT)

def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n",text.strip()) if p.strip()]

def group_images(images,scene_id):
    result=[x for x in images or [] if x.get("scene_id")==scene_id]
    return sorted(result,key=lambda x:(x.get("placement")=="end",
                   int(x.get("paragraph_index") or 0), x["id"]))

def cover_image(images):
    return next((x for x in images or [] if x.get("placement")=="cover"
                 and x.get("scene_id")==COVER_SCENE_ID),None)

def before_images(images):
    return [a for a in images if a.get("placement","start")=="start"]

def after_images(images,para_number):
    return [a for a in images if a.get("placement")=="paragraph"
            and int(a.get("paragraph_index") or 0)==para_number]

def end_images(images,paragraph_count):
    return [a for a in images if a.get("placement")=="end" or
            (a.get("placement")=="paragraph"
             and int(a.get("paragraph_index") or 0)>paragraph_count)]

def colophon(meta: dict) -> list[str]:
    lines=[]
    if meta["author"]:lines.append("Автор: "+meta["author"])
    if meta["year"] is not None:lines.append("Год выпуска: "+str(meta["year"]))
    if meta["edition"]:lines.append("Редакция: "+meta["edition"])
    if meta["publisher"]:lines.append("Издатель: "+meta["publisher"])
    lines.append(meta["credit"])
    return lines
