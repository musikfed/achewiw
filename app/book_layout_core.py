"""Edition typography helpers. No manuscript mutation and no guessed author/year."""
from __future__ import annotations
import re
from datetime import datetime
from pathlib import Path

MAX_EXPORT_IMAGE_BYTES = 50 * 1024 * 1024
MAX_EXPORT_IMAGE_PIXELS = 24_000_000


def verified_illustrations(illustrations):
    """Fail closed before exporting any format; never silently omit an image.

    Does not mutate the catalogue or author's files. The same validation is
    used by EPUB, FB2, PDF and DOCX, including direct Python export callers.
    """
    # Manuscript/Markdown unit tests run before optional graphics dependencies
    # are installed; import Pillow only when actual artwork is being checked.
    if not illustrations:
        return []
    from PIL import Image, UnidentifiedImageError
    result = list(illustrations)
    seen = set()
    for art in result:
        if not isinstance(art, dict):
            raise ValueError("Некорректная запись иллюстрации — выпуск отменён")
        asset_id = str(art.get("id") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", asset_id):
            raise ValueError("Неверный идентификатор иллюстрации — выпуск отменён")
        if asset_id in seen:
            raise ValueError("Повтор идентификатора иллюстрации " + asset_id)
        seen.add(asset_id)
        raw_path = art.get("path")
        if not isinstance(raw_path, (str, Path)) or not raw_path:
            raise ValueError("Иллюстрация " + asset_id + ": нет пути к файлу — выпуск отменён")
        path = Path(raw_path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("Иллюстрация " + asset_id + ": файл отсутствует или небезопасен — выпуск отменён")
        try:
            size = path.stat().st_size
            if size == 0 or size > MAX_EXPORT_IMAGE_BYTES:
                raise ValueError("Иллюстрация " + asset_id + ": размер файла недопустим — выпуск отменён")
            with Image.open(path) as image:
                if image.format != "PNG" or image.width * image.height > MAX_EXPORT_IMAGE_PIXELS:
                    raise ValueError("Иллюстрация " + asset_id + ": ожидается корректный PNG — выпуск отменён")
                image.verify()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise ValueError("Иллюстрация " + asset_id + ": повреждённый PNG — выпуск отменён") from exc
    return result


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
