"""Real book layout, cover, metadata and paragraph placement across all formats."""
from __future__ import annotations
from io import BytesIO
import sys
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from PIL import Image
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
import main


def image():
    buf=BytesIO()
    Image.new("RGB",(72,90),"lightblue").save(buf,format="PNG")
    return buf.getvalue()


def test_edition_cover_toc_and_middle_art(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"achewiw.sqlite3")
    with TestClient(main.app,base_url="http://192.168.0.100:7080",
                    client=("127.0.0.1",12345)) as api:
        p=api.post("/api/projects",json={"title":"Филиниусы"}).json()
        p.update(author_name="Автор теста",publication_year=2026,
                 edition_label="Первое издание",publisher_name="Локальная редакция")
        p["scenes"]=[{"title":"Погоня","text":"Первая сцена.\n\nВторая сцена.\n\nТретья сцена."},
                     {"title":"Развязка","text":"Ключ оказался в сейфе."}]
        p=api.put("/api/projects/"+p["id"],json=p).json()
        scene=p["scenes"][0]["id"]
        base=f"/api/projects/{p['id']}/illustrations"
        cover=api.post(base,data={"scene_id":main.COVER_SCENE_ID,
            "caption":"Ночная улица", "placement":"cover"},
            files={"file":("cover.png",image(),"image/png")})
        assert cover.status_code==201,cover.text
        pic=api.post(base,data={"scene_id":scene,"caption":"Миирус у двери",
            "placement":"paragraph","paragraph_index":"2"},
            files={"file":("chapter.png",image(),"image/png")})
        assert pic.status_code==201,pic.text
        assert pic.json()["paragraph_index"]==2
        second=api.post(base,data={"scene_id":main.COVER_SCENE_ID,"placement":"cover"},
                         files={"file":("other.png",image(),"image/png")})
        assert second.status_code==422
        assert len(api.get(base).json())==2

        epub=api.get(f"/api/projects/{p['id']}/manuscript.epub")
        assert epub.status_code==200,epub.text
        with ZipFile(BytesIO(epub.content)) as z:
            names=z.namelist()
            assert "EPUB/cover.xhtml" in names
            assert "EPUB/title.xhtml" in names
            assert "EPUB/nav.xhtml" in names
            assert "EPUB/credits.xhtml" in names
            opf=z.read("EPUB/content.opf").decode()
            assert "<dc:creator>Автор теста</dc:creator>" in opf
            assert "<dc:date>2026</dc:date>" in opf
            assert "cover-image" in opf
            first=z.read("EPUB/chapter-001.xhtml").decode()
            assert first.index("Вторая сцена") < first.index("Миирус у двери")
            assert first.index("Миирус у двери") < first.index("Третья сцена")
            assert "Содержание" in z.read("EPUB/nav.xhtml").decode()
            assert "ACHEWIW StoryEngine v0.6.0" in z.read("EPUB/credits.xhtml").decode()

        doc=api.get(f"/api/projects/{p['id']}/manuscript.docx")
        assert doc.status_code==200,doc.text
        with ZipFile(BytesIO(doc.content)) as z:
            xml=z.read("word/document.xml").decode()
            assert "Содержание" in xml and "Автор теста" in xml
            assert "2026" in xml
            assert xml.index("Вторая сцена")<xml.index("Миирус у двери")<xml.index("Третья сцена")
            # python-docx deduplicates byte-identical PNGs in the media package.\n            assert len([x for x in z.namelist() if x.startswith("word/media/")])>=1\n            assert xml.count("<w:drawing>")==2

        fb2=api.get(f"/api/projects/{p['id']}/manuscript.fb2")
        assert fb2.status_code==200,fb2.text
        data=fb2.content.decode()
        assert "Содержание" not in data  # FB2 readers generate their TOC from sections.
        assert "2026" in data and "Автор теста" in data
        assert "Миирус у двери" in data and "binary" in data

        pdf=api.get(f"/api/projects/{p['id']}/manuscript.pdf")
        assert pdf.status_code==200,pdf.text
        assert pdf.content.startswith(b"%PDF")
        assert len(pdf.content)>15000
        proof=api.get(f"/api/projects/{p['id']}/manuscript.print.pdf")
        assert proof.status_code==200,proof.text
        assert proof.headers["x-print-status"]=="proof-not-pdfx"
        pre=api.get(f"/api/projects/{p['id']}/publishing-preflight")
        assert pre.json()["publication_metadata_complete"] is True


def test_existing_books_without_year_remain_exportable(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"achewiw.sqlite3")
    with TestClient(main.app,base_url="http://192.168.0.100:7080",
                    client=("127.0.0.1",12345)) as api:
        p=api.post("/api/projects",json={"title":"Старая книга"}).json()
        p["scenes"]=[{"title":"Глава","text":"Текст автора."}]
        p=api.put("/api/projects/"+p["id"],json=p).json()
        assert p["publication_year"] is None
        out=api.get(f"/api/projects/{p['id']}/manuscript.epub")
        assert out.status_code==200,out.text
        with ZipFile(BytesIO(out.content)) as z:
            assert b"<dc:date>" not in z.read("EPUB/content.opf")
        assert api.get(f"/api/projects/{p['id']}/publishing-preflight").json()["publication_metadata_complete"] is False
