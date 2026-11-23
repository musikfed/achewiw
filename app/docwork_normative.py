"""R4.0: conservative, read-only normative preflight for an exported DOCX.

Reports measurable document properties and candidate bibliography sections.
It never certifies compliance with a GOST, never rewrites a document and does
not infer which standard applies to every authored work. Specific edition,
publisher and institutional rules require human confirmation.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import re
import zipfile
from xml.etree import ElementTree as ET

from docx import Document

MAX_DOCX_BYTES = 20 * 1024 * 1024
MAX_PARAGRAPHS = 20000
MAX_SECTIONS = 100
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

# Titles and scopes from official Rosstandart catalog. A profile is a
# *relevant-source suggestion*, never a machine-issued certificate.
STANDARDS = {
    "GOST_R_7_0_100_2018": {
        "number": "ГОСТ Р 7.0.100-2018",
        "purpose": "Библиографическое описание источников",
        "url": "https://protect.gost.ru/gost/details/389dfd5a-535a-458a-81c3-14b729b1cee1",
    },
    "GOST_R_7_0_5_2008": {
        "number": "ГОСТ Р 7.0.5-2008",
        "purpose": "Библиографические ссылки",
        "url": "https://protect.gost.ru/gost/details/2eef7820-44bc-49a4-bf1f-fe51c54039dc",
    },
    "GOST_7_32_2017": {
        "number": "ГОСТ 7.32-2017",
        "purpose": "Отчёт о научно-исследовательской работе",
        "url": "https://protect.gost.ru/gost/details/7d280e43-7036-4a69-8e6e-d15867028343",
    },
    "GOST_R_7_0_97_2025": {
        "number": "ГОСТ Р 7.0.97-2025",
        "purpose": "Организационно-распорядительная документация",
        "url": "https://protect.gost.ru/gost/details/360994e3-9a70-47b9-ab3a-cf21809e26ed",
    },
}
PROFILES = {
    "author_book": {
        "title": "Авторская или учебная книга",
        "standards": ["GOST_R_7_0_100_2018", "GOST_R_7_0_5_2008"],
        "note": "Стандарты библиографического аппарата — при его наличии. Полиграфические параметры согласуются с издателем и макетом; универсального «ГОСТ на все книги» нет.",
    },
    "technical_report": {
        "title": "Отчёт о НИР или техническом исследовании",
        "standards": ["GOST_7_32_2017", "GOST_R_7_0_100_2018", "GOST_R_7_0_5_2008"],
        "note": "ГОСТ 7.32-2017 относится к отчётам указанных типов; требования организации проверяются отдельно.",
    },
    "office_document": {
        "title": "Организационно-распорядительный документ",
        "standards": ["GOST_R_7_0_97_2025"],
        "note": "Не применять организационно-распорядительный стандарт к художественным и учебным книгам.",
    },
    "unclassified": {
        "title": "Без выбранного нормативного профиля",
        "standards": [],
        "note": "Показываем только технические факты, без утверждений о соответствии стандарту.",
    },
}
BIBLIOGRAPHY_TITLE = re.compile(
    r"^(?:список (?:использованн(?:ой|ых) литератур(?:ы|ных источников)|"
    r"литературы|источников)|библиографи(?:я|ческий список)|"
    r"литература|источники|references|bibliography)$",
    re.IGNORECASE,
)
CAPTION = re.compile(r"^(?:рисунок|рис\.|таблица|figure|fig\.|table)\s*\d+\b", re.IGNORECASE)


class NormativePreflightError(ValueError):
    pass


def _xml_elements(archive: zipfile.ZipFile, name: str, tag: str) -> int:
    if name not in archive.namelist():
        return 0
    try:
        root = ET.fromstring(archive.read(name))
    except ET.ParseError as exc:
        raise NormativePreflightError("Некорректная структура документа Word") from exc
    if name.endswith(("footnotes.xml", "endnotes.xml")):
        return sum(1 for el in root.findall(f"w:{tag}", NS)
                   if el.attrib.get("{" + NS["w"] + "}type") not in (
                       "separator", "continuationSeparator", "continuationNotice"))
    return len(root.findall(".//w:" + tag, NS))


def _cm(value) -> float:
    return round(value.cm, 2) if value is not None else 0.0


def available_profiles():
    return [
        {"id": key, "title": val["title"],
         "note": val["note"],
         "standards": [STANDARDS[s] for s in val["standards"]]}
        for key, val in PROFILES.items()
    ]


def audit_docx(raw: bytes, profile: str = "author_book") -> dict:
    if profile not in PROFILES:
        raise NormativePreflightError("Неизвестный нормативный профиль")
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_DOCX_BYTES:
        raise NormativePreflightError("Некорректный DOCX или превышен лимит 20 МиБ")
    if not zipfile.is_zipfile(BytesIO(raw)):
        raise NormativePreflightError("Не является DOCX ZIP")
    try:
        doc = Document(BytesIO(raw))
        if len(doc.paragraphs) > MAX_PARAGRAPHS or len(doc.sections) > MAX_SECTIONS:
            raise NormativePreflightError("Превышен предел проверки структуры DOCX")
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            footnotes = _xml_elements(archive, "word/footnotes.xml", "footnote")
            endnotes = _xml_elements(archive, "word/endnotes.xml", "endnote")
            toc_fields = 0
            cross_refs = 0
            for name in ("word/document.xml",):
                if name in archive.namelist():
                    root = ET.fromstring(archive.read(name))
                    commands = [
                        el.text or "" for el in root.findall(".//w:instrText", NS)
                    ]
                    combined = " ".join(commands).upper()
                    toc_fields = len(re.findall(r"\bTOC\b", combined))
                    cross_refs = len(re.findall(r"\b(?:REF|PAGEREF)\b", combined))
    except (zipfile.BadZipFile, KeyError, ValueError, ET.ParseError) as exc:
        if isinstance(exc, NormativePreflightError):
            raise
        raise NormativePreflightError("Невозможно прочитать структуру DOCX") from exc

    texts = [paragraph.text.strip() for paragraph in doc.paragraphs]
    bibliography_at = next(
        (i for i, text in enumerate(texts) if BIBLIOGRAPHY_TITLE.fullmatch(text)), None
    )
    bibliography_entries = 0
    if bibliography_at is not None:
        for paragraph in doc.paragraphs[bibliography_at + 1:]:
            title = paragraph.text.strip()
            if paragraph.style is not None and paragraph.style.name.lower().startswith("heading"):
                break
            if title:
                bibliography_entries += 1

    captions = sum(bool(CAPTION.match(text)) for text in texts)
    # Section geometry is reported, NOT judged by a universal mandatory A4 rule.
    pages = [
        {"section": i + 1, "width_cm": _cm(s.page_width),
         "height_cm": _cm(s.page_height), "top_cm": _cm(s.top_margin),
         "bottom_cm": _cm(s.bottom_margin),
         "left_cm": _cm(s.left_margin), "right_cm": _cm(s.right_margin)}
        for i, s in enumerate(doc.sections)
    ]
    observations = [
        {"code": "SOURCE_UNCHANGED", "level": "info",
         "message": "Проверка только читает переданный DOCX и не изменяет его."},
        {"code": "MANUAL_LAYOUT_REQUIRED", "level": "review",
         "message": "Шрифты, переносы, обтекание, поля, колонтитулы и страницы следует сверить по экспортированному PDF."},
    ]
    if not toc_fields:
        observations.append(
            {"code": "TOC_NOT_DETECTED", "level": "review",
             "message": "Поле автоматического оглавления Word не обнаружено; это не означает, что оглавления нет."})
    if bibliography_at is None and profile in ("technical_report", "author_book"):
        observations.append(
            {"code": "BIBLIOGRAPHY_NOT_DETECTED", "level": "review",
             "message": "Не найден стандартный заголовок списка источников. Если ссылочный аппарат нужен, проверь вручную."})
    if bibliography_at is not None:
        observations.append(
            {"code": "BIBLIOGRAPHY_MANUAL_FORMAT_CHECK", "level": "review",
             "message": "Наличие списка не доказывает соответствие описаний ГОСТ Р 7.0.100-2018. Проверь поля каждого источника."})
    if profile == "technical_report":
        observations.append(
            {"code": "REPORT_NORMATIVE_REVIEW", "level": "review",
             "message": "Проверить структуру и применимость ГОСТ 7.32-2017 по действующему тексту стандарта."})

    return {
        "profile": profile, "profile_title": PROFILES[profile]["title"],
        "source_sha256": sha256(raw).hexdigest(),
        "status": "needs_editor_review",
        "is_gost_certified": False,
        "standards": [STANDARDS[s] for s in PROFILES[profile]["standards"]],
        "scope_note": PROFILES[profile]["note"],
        "metrics": {
            "paragraphs": len(doc.paragraphs), "tables": len(doc.tables),
            "sections": len(doc.sections), "pictures": len(doc.inline_shapes),
            "captions_candidates": captions, "footnotes": footnotes,
            "endnotes": endnotes, "toc_field_commands": toc_fields,
            "cross_reference_field_commands": cross_refs,
            "bibliography_heading_found": bibliography_at is not None,
            "bibliography_entry_candidates": bibliography_entries,
        },
        "page_geometry": pages,
        "observations": observations,
    }
