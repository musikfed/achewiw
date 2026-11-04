#!/usr/bin/env python3
"""IRREVERSIBLE ACHEWIW-only library reset, no archives left behind.

Read-only preview:
  python3 scripts/erase-studio-library.py --dry-run

Only after studio has stopped:
  python3 scripts/erase-studio-library.py --confirm ERASE_ACHEWIW_LIBRARY_AND_ARCHIVES

Deletes only the exact ACHEWIW SQLite files, own prefixed SQLite snapshots
and /1tbdisk/achewiw/data/illustrations subtree. Monitor metrics and .env stay.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path("/1tbdisk/achewiw")
DATA = ROOT / "data"
CONFIRM = "ERASE_ACHEWIW_LIBRARY_AND_ARCHIVES"


def assert_root() -> None:
    if ROOT.is_symlink() or DATA.is_symlink() or not DATA.is_dir():
        raise RuntimeError("Нужен реальный каталог /1tbdisk/achewiw/data, без ссылок")
    if DATA.resolve() != ROOT / "data":
        raise RuntimeError("Некорректный путь к данным")
    if os.path.ismount(DATA):
        # Standalone mount could be valid, but deletion from unexpected mounts
        # should never be silently attempted.
        raise RuntimeError("data/ является самостоятельным mountpoint — очистка отменена")


def verify_tree(tree: Path) -> None:
    if tree.is_symlink() or os.path.ismount(tree):
        raise RuntimeError("Запрещена ссылка/mount в пути: " + str(tree))
    for base, folders, files in os.walk(tree, followlinks=False):
        for name in folders + files:
            path = Path(base) / name
            if path.is_symlink() or os.path.ismount(path):
                raise RuntimeError("Ссылка или mount внутри данных: " + str(path))


def planned() -> list[Path]:
    assert_root()
    paths = []
    for suffix in ("", "-wal", "-shm", "-journal"):
        target = DATA / ("achewiw.sqlite3" + suffix)
        if target.is_symlink():
            raise RuntimeError("Ссылка в SQLite-пути запрещена")
        if target.is_file():
            paths.append(target)
    backups = DATA / "backups"
    if backups.exists() or backups.is_symlink():
        if not backups.is_dir():
            raise RuntimeError("Небезопасная папка backups")
        verify_tree(backups)
        for item in backups.iterdir():
            if item.is_file() and item.name.startswith(("achewiw-before-reset-", "achewiw-before-delete-")) and item.suffix == ".sqlite3":
                paths.append(item)
    artwork = DATA / "illustrations"
    if artwork.exists() or artwork.is_symlink():
        if not artwork.is_dir():
            raise RuntimeError("Небезопасная папка illustrations")
        verify_tree(artwork)
        paths.append(artwork)
    return paths


def assert_stopped() -> None:
    try:
        proc = subprocess.run(
            ["docker", "ps", "--filter", "label=com.docker.compose.project=achewiw",
             "--format", "{{.ID}}"],
            capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Docker недоступен для проверки, отмена") from exc
    if proc.stdout.strip():
        raise RuntimeError("ACHEWIW ещё запущен; сначала останови только studio")


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--confirm")
    args = parser.parse_args()
    targets = planned()
    print("Сброс только /1tbdisk/achewiw/data. Будут удалены:", flush=True)
    for path in targets:
        print(" -", path, flush=True)
    if not targets:
        print("База и архивы уже отсутствуют")
    if args.dry_run:
        print("DRY RUN: ничего не изменено")
        return
    if args.confirm != CONFIRM:
        raise RuntimeError("Нет точного подтверждения. Операция отменена")
    assert_stopped()
    for path in targets:
        if path.is_dir():
            verify_tree(path)
            shutil.rmtree(path)
        else:
            if path.is_symlink():
                raise RuntimeError("Подмена файла ссылкой: " + str(path))
            path.unlink()
    backups = DATA / "backups"
    if backups.is_dir() and not backups.is_symlink() and not any(backups.iterdir()):
        backups.rmdir()
    print("Готово. SQLite, архивы ACHEWIW и иллюстрации удалены без создания новых архивов.")
    print("Папки других проектов, .env, Ollama, Docker volumes и monitor/ не затронуты.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as exc:
        print("ОСТАНОВЛЕНО:", exc, file=sys.stderr)
        sys.exit(1)
