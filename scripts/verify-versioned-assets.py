#!/usr/bin/env python3
"""Validate exact and versioned local CSS/JS used by ACHEWIW Studio.

The five editorial assets intentionally share one cache key. The sixth asset,
conveyor.js, has an independent cache key so its pipeline UI can be revised
without requiring a rebuild of the editorial bundle.
"""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

EDITORIAL = {
    "/static/style.css", "/static/app.js", "/static/monitor.js",
    "/static/writing_blocks.js", "/static/illustrations.js",
}
INDEPENDENT = {"/static/conveyor.js"}
EXPECTED = EDITORIAL | INDEPENDENT


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "link" and "stylesheet" in a.get("rel", "").split():
            self.urls.append(a.get("href", ""))
        elif tag == "script" and a.get("src"):
            self.urls.append(a["src"])


def check_assets(index_file: Path, static_dir: Path) -> tuple[str, dict[str, str]]:
    if not index_file.is_file() or index_file.is_symlink():
        raise ValueError("Отсутствует безопасный index.html")
    parser = AssetParser()
    parser.feed(index_file.read_text(encoding="utf-8"))
    if len(parser.urls) != len(EXPECTED):
        raise ValueError(
            f"Ожидалось {len(EXPECTED)} CSS/JS ресурсов, найдено {len(parser.urls)}: {parser.urls}"
        )

    versions: dict[str, str] = {}
    for url in parser.urls:
        parsed = urlsplit(url)
        path = parsed.path
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise ValueError("Внешний или некорректный ресурс: " + url)
        if path not in EXPECTED or path in versions:
            raise ValueError("Лишний или повторный ресурс: " + path)
        asset = static_dir / path.removeprefix("/static/")
        if not asset.is_file() or asset.is_symlink():
            raise ValueError("Нет файла ресурса: " + str(asset))
        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) != {"v"} or len(query["v"]) != 1:
            raise ValueError("Не найдена единственная версия ресурса: " + path)
        version = query["v"][0]
        if not version or len(version) > 80:
            raise ValueError("Пустая или слишком длинная версия: " + path)
        versions[path] = version

    if set(versions) != EXPECTED:
        raise ValueError("Список CSS/JS ресурсов отличается от ожидаемого")
    shared = {versions[path] for path in EDITORIAL}
    if len(shared) != 1:
        raise ValueError("Непоследовательные версии пяти ресурсов основного редактора")
    return next(iter(shared)), versions


if __name__ == "__main__":
    try:
        primary, versions = check_assets(Path("app/static/index.html"), Path("app/static"))
    except (ValueError, OSError) as error:
        raise SystemExit("ACHEWIW clean-update: ОСТАНОВЛЕНО: " + str(error))
    print(
        "ACHEWIW clean-update: проверено "
        f"{len(versions)} CSS/JS ресурсов; "
        f"редактор={primary}; конвейер={versions['/static/conveyor.js']}"
    )
