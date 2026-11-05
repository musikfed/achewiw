"""Approved manuscript export: EPUB3 + Markdown with edition metadata and art.

The cover image is a separate project asset; titles, author and year stay real
text, never hallucinated lettering from diffusion. Exports never include AI
drafts that have not been accepted by the author.
"""
from __future__ import annotations

from html import escape
from io import BytesIO
import re
import uuid
from datetime import datetime, timezone
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED

from book_layout_core import (info, colophon, paragraphs, group_images,
    cover_image, before_images, after_images, end_images)


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
    meta=info(project)
    body=[f"# {book['title']}",""]
    body.extend(colophon(meta))
    body.extend(["","## Содержание",""])
    for ch in book["chapters"]:
        body.append(f"- {ch['index']}. {ch['title']}")
    for ch in book["chapters"]:
        body.extend(["",f"## {ch['index']}. {ch['title']}","",ch["text"]])
    return "\n".join(body).rstrip()+"\n"


def _paragraph(part: str) -> str:
    return "<p>"+"<br/>".join(escape(line) for line in part.splitlines())+"</p>"


def manuscript_epub(project, illustrations=None) -> bytes:
    book=chapter_listing(project)
    if not book["chapters"]:
        raise ValueError("No authored scene texts to export")
    meta=info(project)
    title=escape(meta["title"])
    identifier=uuid.uuid5(uuid.NAMESPACE_URL,"achewiw:"+str(project.id))
    manifest=[
        '<item id="style" href="style.css" media-type="text/css"/>',
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="titlepage" href="title.xhtml" media-type="application/xhtml+xml"/>',
        '<item id="credits" href="credits.xhtml" media-type="application/xhtml+xml"/>'
    ]
    files={}
    spine=[]
    images=list(illustrations or [])
    art_by_id={}
    for art in images:
        image_id=str(art.get("id",""))
        if not re.fullmatch(r"[a-f0-9]{32}",image_id):
            continue
        if not art.get("path") or not art["path"].is_file():
            continue
        fname=f"image-{image_id}.png"
        if image_id not in art_by_id:
            files["EPUB/"+fname]=art["path"].read_bytes()
            manifest.append(f'<item id="img{image_id}" href="{fname}" media-type="image/png"'+
                            (' properties="cover-image"' if art.get("placement")=="cover" else '')+'/>')
            art_by_id[image_id]=fname
    def fig(art):
        filename=art_by_id.get(str(art.get("id","")))
        if not filename:
            return ""
        caption=str(art.get("caption") or "")
        return ('<figure><img src="'+filename+'" alt="'+
                escape(caption or "Иллюстрация",quote=True)+'"/>'+
                ('<figcaption>'+escape(caption)+'</figcaption>' if caption else '')+
                '</figure>')
    cover=cover_image(images)
    if cover and str(cover.get("id")) in art_by_id:
        files["EPUB/cover.xhtml"]=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml" lang="ru"><head>'
            '<title>Обложка</title><link rel="stylesheet" href="style.css"/></head>'
            '<body><section class="cover">'+fig(cover)+'</section></body></html>'
        )
        manifest.append('<item id="coverpage" href="cover.xhtml" media-type="application/xhtml+xml"/>')
        spine.append('<itemref idref="coverpage"/>')
    title_parts=[f'<h1>{title}</h1>']
    if meta["author"]:title_parts.append('<p class="author">'+escape(meta["author"])+'</p>')
    if meta["edition"]:title_parts.append('<p>'+escape(meta["edition"])+'</p>')
    if meta["publisher"]:title_parts.append('<p>'+escape(meta["publisher"])+'</p>')
    if meta["year"] is not None:title_parts.append('<p>'+str(meta["year"])+'</p>')
    files["EPUB/title.xhtml"]=(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="ru"><head><title>'+
        title+'</title><link rel="stylesheet" href="style.css"/></head>'
        '<body><section class="titlepage">'+"".join(title_parts)+'</section></body></html>')
    spine.append('<itemref idref="titlepage"/>')
    nav=[]
    for position, chapter in enumerate(book["chapters"],1):
        name=f"chapter-{position:03}.xhtml"
        ch_title=escape(chapter["title"])
        arts=group_images(images,chapter["scene_id"])
        parts=paragraphs(chapter["text"])
        content=["".join(fig(a) for a in before_images(arts))]
        for paragraph_no,part in enumerate(parts,1):
            content.append(_paragraph(part))
            content.extend(fig(a) for a in after_images(arts,paragraph_no))
        content.extend(fig(a) for a in end_images(arts,len(parts)))
        files["EPUB/"+name]=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml" lang="ru">'
            '<head><meta charset="UTF-8"/><title>'+ch_title+
            '</title><link rel="stylesheet" href="style.css" type="text/css"/></head>'
            '<body><section><h2>'+ch_title+'</h2>'+"".join(content)+
            '</section></body></html>'
        )
        manifest.append(f'<item id="ch{position}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="ch{position}"/>')
        nav.append(f'<li><a href="{name}">{chapter["index"]}. {ch_title}</a></li>')
    nav.append('<li><a href="credits.xhtml">Выходные сведения</a></li>')
    files["EPUB/nav.xhtml"]=(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" lang="ru">'
        '<head><title>Содержание</title><link rel="stylesheet" href="style.css"/></head>'
        '<body><nav epub:type="toc" id="toc"><h1>Содержание</h1>'
        '<ol>'+"".join(nav)+'</ol></nav></body></html>'
    )
    spine.insert(2 if cover and str(cover.get("id")) in art_by_id else 1,
                 '<itemref idref="nav"/>')
    files["EPUB/credits.xhtml"]=(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="ru"><head>'
        '<title>Выходные сведения</title><link rel="stylesheet" href="style.css"/></head>'
        '<body><section><h2>Выходные сведения</h2>'+
        "".join(_paragraph(x) for x in colophon(meta))+
        '</section></body></html>'
    )
    spine.append('<itemref idref="credits"/>')
    files["EPUB/style.css"]=(
        'body{font-family:serif;line-height:1.6;margin:1em;}'
        'h2{text-align:center;margin:2em 0 1em;}p{text-indent:1em;}'
        'figure{text-align:center;margin:1.2em auto;}figure img{max-width:100%;height:auto;}'
        'figcaption{font-style:italic;font-size:.9em;}'
        '.titlepage{text-align:center;margin-top:25%;}.titlepage h1{font-size:2em;}'
        '.cover img{max-height:95vh;object-fit:contain;}'
    )
    files["META-INF/container.xml"]=(
        '<?xml version="1.0"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="EPUB/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    modified=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dc='<dc:identifier id="bookid">urn:uuid:'+str(identifier)+'</dc:identifier>'
    dc+='<dc:title>'+title+'</dc:title><dc:language>ru</dc:language>'
    if meta["author"]:dc+='<dc:creator>'+escape(meta["author"])+'</dc:creator>'
    if meta["publisher"]:dc+='<dc:publisher>'+escape(meta["publisher"])+'</dc:publisher>'
    if meta["year"] is not None:dc+='<dc:date>'+str(meta["year"])+'</dc:date>'
    dc+='<dc:contributor>'+escape(meta["credit"])+'</dc:contributor>'
    files["EPUB/content.opf"]=(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'+dc+
        '<meta property="dcterms:modified">'+modified+'</meta>'+
        '</metadata><manifest>'+"".join(manifest)+
        '</manifest><spine>'+"".join(spine)+'</spine></package>'
    )
    output=BytesIO()
    with ZipFile(output,"w") as archive:
        archive.writestr("mimetype","application/epub+zip",compress_type=ZIP_STORED)
        for filename,data in files.items():
            archive.writestr(filename,data,compress_type=ZIP_DEFLATED)
    return output.getvalue()
