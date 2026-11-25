"""Read-only, evidence-based print-proof preflight for ACHEWIW.

Operates on already-rendered PDF bytes. Does not touch DOCWORK, manuscript,
Ollama, or published files. PDF page positions are measured with PyMuPDF;
TOC claims are compared only when a printed page number can be parsed
unambiguously. Ambiguity blocks an automatic 'ready' verdict.
"""
from __future__ import annotations

import io
import re
import hashlib
from typing import Any

import fitz

from page_fit_advisor import PageEvidence, recommend

PAGE_LIMIT = 1200
MAX_PDF_BYTES = 65 * 1024 * 1024
NUM_RE = re.compile(r"(?:\.{2,}|…{2,}|[ \t]{2,})\s*(\d{1,5})\s*$")
SPACE_RE = re.compile(r"(?<!\S)[ \t]{3,}|\S {5,}\S")
ROMAN_RE = re.compile(r"^(?:[IVXLCDM]+)\.\s+", re.I)


def _compact(s: str) -> str:
    return " ".join(str(s).replace("\u00ad", "").replace("\u00a0", " ").split()).casefold()


def _heading_matches(text: str, expected: str) -> bool:
    haystack = _compact(text)
    needle = _compact(expected)
    return bool(needle and (needle in haystack or needle[:85] in haystack and len(needle)>85))


def _numbered_title(text: str) -> str:
    return re.sub(r"^(?:глава\s+)?\d{1,3}\s*[.\-:]\s*", "",_compact(text),flags=re.I)


def _toc_page(lines: list[str], heading: str) -> int | None:
    title = _numbered_title(heading)
    for line in lines:
        m = NUM_RE.search(line)
        if m and (title in _numbered_title(line) or (_numbered_title(line) in title and len(line)>15)):
            return int(m.group(1))
    return None


def _line_stats(page: fitz.Page) -> tuple[float,float,bool]:
    """Measure content coverage while excluding a modest page-header/footer band."""
    raw=page.get_text("dict")
    box=page.rect
    top=box.height*0.11
    bottom=box.height*0.90
    ys=[]
    oversized_gaps=False
    for block in raw.get("blocks",[]):
        if block.get("type")!=0:continue
        for line in block.get("lines",[]):
            spans=line.get("spans",[])
            line_text="".join(str(sp.get("text","")) for sp in spans)
            if not line_text.strip():continue
            bounds=line.get("bbox",(0,0,0,0))
            y0,y1=bounds[1],bounds[3]
            if y1<=top or y0>=bottom:continue
            ys.extend((max(y0,top),min(y1,bottom)))
            if SPACE_RE.search(line_text):oversized_gaps=True
    if not ys:return 0.0,0.0,oversized_gaps
    # Spatial coverage: max vertical extent, not glyph ink area.
    occupancy=max(0,min(1,(max(ys)-top)/(bottom-top)))
    return round(occupancy,3),round(max(ys)-min(ys),1),oversized_gaps


def inspect_pdf(pdf: bytes, *, chapter_titles: list[str] | None=None,
                edition_label: str | None=None, expected_pages: int | None=None,
                max_pages: int=PAGE_LIMIT) -> dict[str,Any]:
    """Read-only PDF QA with conservative, explicit limitations."""
    if not isinstance(pdf,bytes) or not pdf.startswith(b"%PDF-") or len(pdf)>MAX_PDF_BYTES:
        raise ValueError("PDF missing or exceeds preflight limit")
    if max_pages>PAGE_LIMIT or max_pages<1:raise ValueError("Invalid page bound")
    findings=[]
    def finding(code,severity,page,message):
        findings.append({"code":code,"severity":severity,"page":page,"message":message})
    with fitz.open(stream=pdf,filetype="pdf") as doc:
        count=doc.page_count
        if not 1<=count<=max_pages:
            raise ValueError("PDF page count is outside safety bounds")
        if doc.is_encrypted:
            raise ValueError("Encrypted PDFs cannot be preflighted")
        metadata=doc.metadata or {}
        if expected_pages is not None and count!=expected_pages:
            finding("PAGE_COUNT_MISMATCH","error",None,
                    f"PDF: {count} страниц; ожидается: {expected_pages}")
        source_text=[]
        measurements=[]
        for idx in range(count):
            pg=doc.load_page(idx)
            text=pg.get_text("text",sort=True)
            source_text.append(text)
            occupied,height,gaps=_line_stats(pg)
            measurements.append({"pdf_page":idx+1,"occupied_ratio":occupied,
                "content_span_pt":height,"wide_spaces_detected":gaps,
                "media_box_pt":[round(pg.rect.width,2),round(pg.rect.height,2)]})
        contents_indices=[i for i,t in enumerate(source_text[:min(count,25)])
                          if "содержание" in _compact(t) or "оглавление" in _compact(t) or "contents" in _compact(t)]
        toc_lines=[]
        if contents_indices:
            # One or more adjacent TOC pages; do not mistake later chapters for TOC.
            first=contents_indices[0]
            for i in range(first,min(count,first+15)):
                if i>first and i>first+1 and ("глава " in _compact(source_text[i]) and not NUM_RE.search(source_text[i])):
                    break
                toc_lines.extend(source_text[i].splitlines())
        chapter_rows=[]
        for title in chapter_titles or []:
            title=str(title).strip()
            if not title:continue
            observed=None
            for i,t in enumerate(source_text):
                if i in contents_indices:
                    continue
                compact=_compact(t)
                if _heading_matches(compact,title):
                    observed=i+1
                    break
            listed=_toc_page(toc_lines,title) if toc_lines else None
            chapter_rows.append({"title":title,"actual_pdf_page":observed,
                                 "toc_page":listed,"match":listed==observed if listed is not None and observed is not None else None})
            if observed is None:
                finding("CHAPTER_NOT_FOUND","warning",None,"Заголовок не найден в PDF: "+title)
            if listed is None:
                finding("TOC_PAGE_UNKNOWN","warning",observed,
                        "Номер главы в печатном содержании не извлечён однозначно: "+title)
            elif observed is not None and listed!=observed:
                finding("TOC_PAGE_MISMATCH","error",observed,
                        f"{title}: в содержании {listed}, фактическая PDF-страница {observed}")
        for i,row in enumerate(measurements):
            if row["occupied_ratio"]<0.09 and i>0:
                next_text=source_text[i+1] if i+1<count else ""
                section=bool(re.search(r"\b(?:часть|раздел)\s+[IVXLCDM\d]+",next_text,re.I))
                decision=recommend(PageEvidence(row["pdf_page"],row["occupied_ratio"],
                    next_page_starts_part=section,
                    justified_large_word_spaces=row["wide_spaces_detected"]))
                # Blank final page is not automatically deleted.
                severity="warning"
                finding("NEAR_EMPTY_PAGE",severity,i+1,
                        f"Почти пустая страница; {decision['action']}. Требуется просмотр.")
                row["layout_recommendation"]=decision
        for row in measurements:
            if row["wide_spaces_detected"]:
                finding("LARGE_WORD_SPACES","warning",row["pdf_page"],
                        "Возможны растянутые интервалы/ручные пробелы; проверить верстку, не дописывать текст")
        label=(edition_label or "").strip()
        subject=str(metadata.get("subject") or "")
        if label and subject and re.search(r"\br\d{2}\b",subject,re.I) and label.casefold() not in subject.casefold():
            finding("STALE_PDF_EDITION","warning",None,
                    "Версия в PDF metadata не совпадает с выбранной редакцией")
        if len(measurements)>0 and count>1 and measurements[-1]["occupied_ratio"]<0.09:
            finding("FINAL_EMPTY_PAGE","warning",count,"Последняя страница практически пустая; ручная проверка перед выпуском")
        return {"pdf_sha256":hashlib.sha256(pdf).hexdigest(),"page_count":count,
          "page_size_pt":measurements[0]["media_box_pt"],"metadata":{"title":metadata.get("title"),
                "subject":subject,"author":metadata.get("author")},
          "toc_found":bool(contents_indices),"headings":chapter_rows,
          "pages":measurements,"findings":findings,
          "ready":not any(item["severity"]=="error" for item in findings),
          "requires_editor_review":bool(findings),
          "source":"measured-pdf","manuscript_mutated":False}
