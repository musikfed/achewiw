"""Render a *real manuscript* from authored scene texts, not a QA report.

No AI call here; exported EPUB and Markdown contain only approved project
scene texts. Missing scenes remain visible as editing tasks in the preview.
"""
from __future__ import annotations

from html import escape
from io import BytesIO
import re
import uuid
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED

EPUB_NS = "http://www.idpf.org/2007/opf"


def chapter_listing(project) -> dict:
    chapters = []
    missing = []
    for i, scene in enumerate(project.scenes, 1):
        title = str(scene.title or f"Сцена {i}")
        text = str(scene.text or "").strip()
        if text:
            chapters.append({
                "scene_id": scene.id, "index": i, "title": title, "text": text,
                "words": len(re.findall(r"\b[\w-]+\b", text, re.UNICODE)),
            })
        else:
            missing.append({"scene_id": scene.id, "index": i, "title": title})
    return {
        "title": project.title,
        "chapters": chapters,
        "unwritten": missing,
        "total_scenes": len(project.scenes),
        "written_scenes": len(chapters),
        "total_words": sum(ch["words"] for ch in chapters),
    }


def manuscript_markdown(project) -> str:
    book = chapter_listing(project)
    if not book["chapters"]:
        raise ValueError("No authored scene texts to export")
    body = [f"# {book['title']}"]
    for chapter in book["chapters"]:
        body.extend(["", f"## {chapter['index']}. {chapter['title']}", "",
                     chapter["text"]])
    return "\n".join(body).rstrip() + "\n"


def _paragraphs(text: str) -> str:
    # Every user-authored character is escaped; XHTML cannot execute HTML/JS.
    blocks = re.split(r"\n\s*\n", text.strip())
    return "\n".join(
        "<p>" + "<br/>".join(escape(line) for line in block.splitlines()) + "</p>"
        for block in blocks if block.strip()
    )


def manuscript_epub(project) -> bytes:
    book = chapter_listing(project)
    if not book["chapters"]:
        raise ValueError("No authored scene texts to export")
    title = escape(str(project.title))
    identifier = uuid.uuid5(uuid.NAMESPACE_URL, "achewiw:" + str(project.id))
    manifest = ['<item id="style" href="style.css" media-type="text/css"/>',
                '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>']
    spine = []
    nav = []
    files = {}
    for sequence, chapter in enumerate(book["chapters"], 1):
        name = f"chapter-{sequence:03}.xhtml"
        chapter_title = escape(chapter["title"])
        inner = _paragraphs(chapter["text"])
        files[f"EPUB/{name}"] = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" lang="ru">'
            '<head><meta charset="UTF-8"/><title>' + chapter_title +
            '</title><link rel="stylesheet" href="style.css" type="text/css"/></head>'
            '<body><section><h2>' + chapter_title + '</h2>' + inner +
            '</section></body></html>'
        )
        manifest.append(f'<item id="ch{sequence}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="ch{sequence}"/>')
        nav.append(f'<li><a href="{name}">{chapter_title}</a></li>')
    files["EPUB/nav.xhtml"] = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="ru">'
        '<head><title>Оглавление</title></head>'
        '<body><nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc" '
        'id="toc"><h1>Оглавление</h1><ol>' + "".join(nav) +
        '</ol></nav></body></html>'
    )
    files["EPUB/style.css"] = (
        'body{font-family:serif;line-height:1.6;margin:1em;}'
        'h2{text-align:center;margin:2em 0 1em;}p{text-indent:1.2em;}'
    )
    files["META-INF/container.xml"] = (
        '<?xml version="1.0"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="EPUB/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    files["EPUB/content.opf"] = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="bookid">urn:uuid:' + str(identifier) + '</dc:identifier>'
        '<dc:title>' + title + '</dc:title><dc:language>ru</dc:language>'
        '</metadata><manifest>' + "".join(manifest) +
        '</manifest><spine>' + "".join(spine) + '</spine></package>'
    )
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        for path, data in files.items():
            archive.writestr(path, data, compress_type=ZIP_DEFLATED)
    return output.getvalue()
