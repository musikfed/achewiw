"""Deletion is scoped to user's explicitly requested ACHEWIW book library only."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import sys
import sqlite3

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
import main


def loader():
    path=ROOT/"scripts/erase-studio-library.py"
    spec=importlib.util.spec_from_file_location("achewiw_wipe_scoped",path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_only_library_files_are_selected_for_clean_start(tmp_path,monkeypatch):
    erase=loader()
    monkeypatch.setattr(erase,"ROOT",tmp_path)
    data=tmp_path/"data";data.mkdir()
    monkeypatch.setattr(erase,"DATA",data)
    for name in ("achewiw.sqlite3","search.sqlite3","work.sqlite3",
                 "achewiw.sqlite3-wal","search.sqlite3-shm"):
        (data/name).write_bytes(b"db")
    artwork=data/"illustrations"/("a"*32)
    artwork.mkdir(parents=True)
    (artwork/"image.png").write_bytes(b"png")
    backup=data/"backups";backup.mkdir()
    for name in ("achewiw-before-delete-old.sqlite3",
                 "achewiw-before-reset-old.sqlite3",
                 "before-story-storage-old.sqlite3",
                 "other-person.sqlite3"):
        (backup/name).write_bytes(b"backup")
    (data/"comfyui-runtime"/"src").mkdir(parents=True)
    (data/"comfyui-runtime"/"src"/"checkpoint.safetensors").write_bytes(b"preserve")
    output=data/"comfyui-runtime"/"src"/"output"
    output.mkdir()
    own_generated=output/"achewiw_local_00001_.png"
    own_generated.write_bytes(b"old-generated-book-image")
    foreign_generated=output/"other_client_00001_.png"
    foreign_generated.write_bytes(b"keep")
    (data/"monitor").mkdir()
    (data/"monitor"/"host.json").write_text("{}")
    (data/"comfyui-control.token").write_text("preserve")
    (tmp_path/"unrelated_project.sqlite3").write_bytes(b"preserve")
    targets=set(erase.planned())
    assert data/"achewiw.sqlite3" in targets
    assert data/"search.sqlite3" in targets
    assert data/"work.sqlite3" in targets
    assert data/"achewiw.sqlite3-wal" in targets
    assert artwork.parent in targets
    assert backup/"before-story-storage-old.sqlite3" in targets
    assert backup/"achewiw-before-delete-old.sqlite3" in targets
    assert backup/"achewiw-before-reset-old.sqlite3" in targets
    assert backup/"other-person.sqlite3" not in targets
    assert data/"comfyui-runtime" not in targets
    assert own_generated in targets
    assert foreign_generated not in targets
    assert output not in targets
    assert data/"comfyui-runtime"/"src"/"checkpoint.safetensors" not in targets
    assert data/"comfyui-control.token" not in targets
    assert data/"monitor" not in targets
    assert tmp_path/"unrelated_project.sqlite3" not in targets


def test_epoch_endpoint_is_absent_until_explicit_reset(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"achewiw.sqlite3")
    with TestClient(main.app,base_url="http://192.168.0.100:7080",
                    client=("127.0.0.1",4570)) as client:
        url="/api/storage/reset-epoch"
        response=client.get(url)
        assert response.status_code==200
        assert response.json()=={"epoch":None}
        (tmp_path/".achewiw-library-epoch").write_text("d"*40)
        assert client.get(url).json()=={"epoch":"d"*40}
        (tmp_path/".achewiw-library-epoch").write_text("unsafe../")
        assert client.get(url).json()=={"epoch":None}


def test_fresh_restart_is_git_safe_and_not_system_global():
    shell=(ROOT/"scripts/reinstall-studio-fresh.sh").read_text()
    js=(ROOT/"app/static/app.js").read_text()
    assert "git merge --ff-only" in shell
    assert "git reset --hard" not in shell
    assert "docker system prune" not in shell
    assert "compose build --no-cache studio" in shell
    assert "scripts/erase-studio-library.py --dry-run" in shell
    assert 'scripts/erase-studio-library.py --confirm "$TOKEN"' in shell
    assert "ACHEWIW_LIBRARY_AND_ARCHIVES" in shell
    assert "achewiw-library-epoch" in shell
    assert "resetBrowserAfterFreshLibrary" in js
    assert "key.startsWith('achewiw.')" in js
    assert "achewiw.v9.comfyControlJob" in js
    assert "await resetBrowserAfterFreshLibrary()" in js
