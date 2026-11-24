#!/usr/bin/env python3
"""A1.3: attach explicitly selected external files to a verified SQLite rescue ZIP.

This tool NEVER modifies the existing rescue archive, books, CDR/PSD projects,
or the source directory. The caller supplies each scope with --include NAME=PATH.
No claim of 'complete publication snapshot': sources might still be missing.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import zipfile

MAX_FILES = 2000
MAX_BYTES = 8 * 1024 ** 3
CHUNK = 1024 * 1024


def digest_stream(stream):
    sha = sha256()
    length = 0
    for block in iter(lambda: stream.read(CHUNK), b""):
        sha.update(block)
        length += len(block)
    return sha.hexdigest(), length


def selected_files(entries, source_archive: Path, output: Path):
    if not entries:
        raise ValueError("Укажи хотя бы одну папку/файл через --include NAME=PATH")
    found = []
    seen = set()
    total = 0
    for item in entries:
        name, sep, raw = item.partition("=")
        if not sep or not name or not name.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Формат --include: short-name=/absolute/path")
        path = Path(raw)
        if not path.is_absolute() or not path.exists() or path.is_symlink():
            raise ValueError("Источник отсутствует, относителен или является ссылкой")
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for p in candidates:
            if p.is_symlink():
                raise ValueError("Символическая ссылка запрещена: "+str(p))
            if p.is_dir():
                continue
            if not p.is_file():
                raise ValueError("Необычный объект файловой системы: "+str(p))
            if p.resolve() in (source_archive.resolve(), output.resolve()):
                raise ValueError("Архив нельзя включать внутрь самого себя")
            # No path traversal in archive; preserve only relative named scope.
            relative = p.relative_to(path) if path.is_dir() else Path(p.name)
            archive_name = "external/" + name + "/" + relative.as_posix()
            if archive_name in seen:
                raise ValueError("Повтор имени внутри архива: "+archive_name)
            seen.add(archive_name)
            size = p.stat().st_size
            total += size
            if total > MAX_BYTES or len(found) >= MAX_FILES:
                raise ValueError("Превышен лимит файлов/объёма внешнего архива")
            found.append((p, archive_name))
    if not found:
        raise ValueError("Выбранные источники не содержат файлов")
    return found


def create_extended_archive(source: Path, target: Path, entries: list[str]):
    if not source.is_file():
        raise FileNotFoundError(source)
    if target.exists():
        raise FileExistsError(target)
    if source.resolve() == target.resolve():
        raise ValueError("Нельзя перезаписывать исходный архив")
    files = selected_files(entries, source, target)
    with zipfile.ZipFile(source, "r") as z:
        required = {"database/achewiw-consistent.sqlite3",
                    "report/job-rescue-report.json"}
        if not required.issubset(z.namelist()) or z.testzip() is not None:
            raise ValueError("Входной rescue ZIP не прошёл проверку")
        report = json.loads(z.read("report/job-rescue-report.json"))
        with z.open("database/achewiw-consistent.sqlite3") as handle:
            db_hash, _ = digest_stream(handle)
        if db_hash != report["sqlite_sha256"]:
            raise ValueError("SQLite checksum входного архива не совпал")
    target.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        created = True
        with os.fdopen(fd, "wb") as output, zipfile.ZipFile(
            output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as bundle:
            with zipfile.ZipFile(source, "r") as original:
                for name in original.namelist():
                    if name.startswith("external/") or name == "report/external-manifest.json":
                        raise ValueError("Поддерживается только исходный rescue ZIP")
                    # Read archive member by streaming, avoid large memory allocations.
                    with original.open(name) as src, bundle.open(name, "w") as dest:
                        for block in iter(lambda: src.read(CHUNK), b""):
                            dest.write(block)
            asset_rows = []
            for path, name in files:
                before = path.stat()
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError("Источник не является обычным файлом")
                sha = sha256()
                byte_count = 0
                with path.open("rb") as src, bundle.open(name, "w") as dest:
                    for block in iter(lambda: src.read(CHUNK), b""):
                        sha.update(block)
                        byte_count += len(block)
                        dest.write(block)
                after = path.stat()
                if (before.st_size != byte_count or after.st_size != byte_count
                    or before.st_mtime_ns != after.st_mtime_ns):
                    raise ValueError("Источник изменился во время копирования: "+str(path))
                asset_rows.append({
                    "archive_path": name, "size_bytes": byte_count,
                    "sha256": sha.hexdigest(), "original_path": str(path),
                })
            manifest = {
                "scope": "explicitly selected external files only",
                "publication_snapshot_complete": False,
                "coverage_verified": False,
                "reason": "Полнота материалов книги не проверена автоматически",
                "files": asset_rows,
            }
            bundle.writestr(
                "report/external-manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        with zipfile.ZipFile(target) as ready:
            if ready.testzip() is not None:
                raise ValueError("Ошибка CRC в итоговом архиве")
            saved = json.loads(ready.read("report/external-manifest.json"))
            for row in saved["files"]:
                with ready.open(row["archive_path"]) as stream:
                    checksum, byte_count = digest_stream(stream)
                if checksum != row["sha256"] or byte_count != row["size_bytes"]:
                    raise ValueError("Ошибка SHA256 включённого файла")
        return {"archive": str(target), "assets": len(files),
                "sha256": sha256(target.read_bytes()).hexdigest()
                if target.stat().st_size <= 64 * 1024 * 1024 else None,
                "publication_snapshot_complete": False}
    except Exception:
        if created and target.exists():
            target.unlink()
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, type=Path, help="Existing verified rescue ZIP")
    ap.add_argument("--out", required=True, type=Path, help="New ZIP; must not exist")
    ap.add_argument("--include", action="append", default=[], metavar="NAME=PATH",
                    help="Explicit file or folder; repeat for each source group")
    a = ap.parse_args()
    try:
        print(json.dumps(create_extended_archive(a.source, a.out, a.include),
                         ensure_ascii=False, indent=2))
    except (OSError, ValueError, zipfile.BadZipFile, KeyError) as exc:
        ap.exit(2, "Архив внешних файлов НЕ создан: "+str(exc)+"\n")


if __name__ == "__main__":
    main()
