"""Real project PNG artwork → PDF, DOCX, EPUB + safe scoped reset."""
import importlib.util
from io import BytesIO
import os
from pathlib import Path
import sqlite3
import sys
from zipfile import ZipFile
from PIL import Image
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
import main
from image_engine import extract_checkpoints, pick_checkpoint, workflow


def png():
    output=BytesIO()
    Image.new("RGB",(24,18),"white").save(output,format="PNG")
    return output.getvalue()


def test_actual_chapter_image_and_export(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"test.sqlite3")
    with TestClient(main.app,base_url="http://192.168.0.100:7080",
                    client=("127.0.0.1",4567)) as client:
        project=client.post("/api/projects",json={"title":"Книга с иллюстрацией","work_type":"technical"}).json()
        scene_id="a"*32
        project["scenes"]=[{"id":scene_id,"title":"Глава 1","text":"Русская глава."}]
        assert client.put("/api/projects/"+project["id"],json=project).status_code==200
        url=f"/api/projects/{project['id']}/illustrations"
        uploaded=client.post(url,data={"scene_id":scene_id,"caption":"Схема сети",
               "placement":"end"},files={"file":("diagram.png",png(),"image/png")})
        assert uploaded.status_code==201,uploaded.text
        asset=uploaded.json()["id"]
        items=client.get(url).json()
        assert len(items)==1 and items[0]["placement"]=="end"
        served=client.get(url+"/"+asset+".png")
        assert served.status_code==200 and served.content.startswith(b"\x89PNG")
        epub=client.get(f"/api/projects/{project['id']}/manuscript.epub")
        assert epub.status_code==200,epub.text
        with ZipFile(BytesIO(epub.content)) as archive:
            assert f"EPUB/image-{asset}.png" in archive.namelist()
            assert "Схема сети" in archive.read("EPUB/chapter-001.xhtml").decode()
        doc=client.get(f"/api/projects/{project['id']}/manuscript.docx")
        assert doc.status_code==200,doc.text
        with ZipFile(BytesIO(doc.content)) as archive:
            assert "word/media/image1.png" in archive.namelist()
        pdf=client.get(f"/api/projects/{project['id']}/manuscript.print.pdf")
        assert pdf.status_code==200,pdf.text
        assert pdf.content.startswith(b"%PDF")
        assert client.delete(url+"/"+asset).status_code==204
        assert client.get(url).status_code==200
        assert client.get(url).json()==[]


def test_reject_malicious_and_oversized_picture(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"test.sqlite3")
    with TestClient(main.app,base_url="http://192.168.0.100:7080",
                    client=("127.0.0.1",4567)) as client:
        p=client.post("/api/projects",json={"title":"Only"}).json()
        sid="b"*32
        p["scenes"]=[{"id":sid,"title":"1","text":"test"}]
        assert client.put("/api/projects/"+p["id"],json=p).status_code==200
        base=f"/api/projects/{p['id']}/illustrations"
        bad=client.post(base,data={"scene_id":sid},
                 files={"file":("evil.png",b"<script>alert(1)</script>","image/png")})
        assert bad.status_code==422
        invalid=client.post(base,data={"scene_id":"c"*32},
                 files={"file":("good.png",png(),"image/png")})
        assert invalid.status_code==422
        too_big=client.post(base,data={"scene_id":sid},
                 files={"file":("big.png",b"x"*(12*1024*1024+1),"image/png")})
        assert too_big.status_code==422


def test_comfyui_selects_actual_available_checkpoint():
    info={"CheckpointLoaderSimple":{"input":{"required":{"ckpt_name":[[
      "sd15.safetensors","sdxl-book.safetensors"],{}]}}}}
    assert extract_checkpoints(info)==["sd15.safetensors","sdxl-book.safetensors"]
    chosen=pick_checkpoint(extract_checkpoints(info))
    assert chosen=="sdxl-book.safetensors"
    plan=workflow("книжная иллюстрация",chosen,123)
    assert plan["4"]["inputs"]["ckpt_name"]==chosen
    assert plan["7"]["inputs"]["width"]==1024


def test_erase_scoped_to_exact_library(tmp_path,monkeypatch):
    path=Path(__file__).resolve().parents[1]/"scripts"/"erase-studio-library.py"
    spec=importlib.util.spec_from_file_location("wipe",path)
    reset=importlib.util.module_from_spec(spec);spec.loader.exec_module(reset)
    monkeypatch.setattr(reset,"ROOT",tmp_path)
    monkeypatch.setattr(reset,"DATA",tmp_path/"data")
    d=tmp_path/"data";d.mkdir()
    (d/"achewiw.sqlite3").write_bytes(b"test")
    backups=d/"backups";backups.mkdir()
    own=backups/"achewiw-before-delete-abc.sqlite3";own.write_bytes(b"saved")
    stranger=backups/"unrelated.sqlite3";stranger.write_bytes(b"keep")
    (d/"monitor").mkdir()
    (d/"monitor"/"host.json").write_text("{}")
    pictures=d/"illustrations"/("a"*32);pictures.mkdir(parents=True)
    (pictures/"a.png").write_bytes(png())
    paths=reset.planned()
    assert d/"achewiw.sqlite3" in paths
    assert own in paths
    assert stranger not in paths
    assert d/"illustrations" in paths
    monkeypatch.setattr(reset,"assert_stopped",lambda:None)
    for item in paths:
        if item.is_dir():
            import shutil;shutil.rmtree(item)
        else:item.unlink()
    assert stranger.exists()
    assert (d/"monitor"/"host.json").exists()
    assert not (d/"achewiw.sqlite3").exists()


def test_refuse_symlinks(tmp_path,monkeypatch):
    path=Path(__file__).resolve().parents[1]/"scripts"/"erase-studio-library.py"
    spec=importlib.util.spec_from_file_location("wipe",path)
    reset=importlib.util.module_from_spec(spec);spec.loader.exec_module(reset)
    monkeypatch.setattr(reset,"ROOT",tmp_path)
    monkeypatch.setattr(reset,"DATA",tmp_path/"data")
    d=tmp_path/"data";d.mkdir()
    external=tmp_path/"external";external.mkdir()
    (d/"illustrations").symlink_to(external)
    with pytest.raises(RuntimeError):
        reset.planned()
