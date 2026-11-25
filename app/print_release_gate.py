"""Fail-closed publication gate for measured final PDF + updateable DOCX.

The DOCX TOC field alone is *not* evidence that Word/LibreOffice refreshed it.
The office-reflow marker must come from the controlled exporter (and its files).
All chapter page numbers are compared with actually rendered PDF pages.
"""
from __future__ import annotations

from io import BytesIO
from zipfile import BadZipFile,ZipFile
from xml.etree import ElementTree
import hashlib

import fitz

from print_proof_preflight import inspect_pdf

W="http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def inspect_docx_toc(document: bytes)->dict:
    if not isinstance(document,bytes) or len(document)>65_000_000:
        raise ValueError("DOCX missing or exceeds size limit")
    try:
        with ZipFile(BytesIO(document)) as archive:
            xml=ElementTree.fromstring(archive.read("word/document.xml"))
            settings=ElementTree.fromstring(archive.read("word/settings.xml"))
    except (BadZipFile,KeyError,ElementTree.ParseError,ValueError) as exc:
        raise ValueError("Not a valid Word DOCX with settings") from exc
    instructions=[str(el.text or "") for el in xml.iter(f"{{{W}}}instrText")]
    is_toc=any("TOC" in value.upper().split() for value in instructions)
    update=any(str(el.attrib.get(f"{{{W}}}val","")).casefold() in ("1","true","on")
               for el in settings.iter(f"{{{W}}}updateFields"))
    return {"toc_field":is_toc,"update_on_open":update,
            "field_instructions":instructions[:10],
            "sha256":hashlib.sha256(document).hexdigest()}


def _toc_links(pdf: bytes, page_numbers: list[int]) -> dict:
    """Probe existence of internal PDF links on actual TOC pages, never invent them."""
    with fitz.open(stream=pdf,filetype="pdf") as doc:
        counts=[]
        for number in page_numbers:
            if not 1<=number<=doc.page_count:continue
            links=[item for item in doc.load_page(number-1).get_links()
                   if item.get("kind")==fitz.LINK_GOTO and isinstance(item.get("page"),int)]
            counts.append({"pdf_page":number,"goto_links":len(links)})
        return {"pages":counts,"total_links":sum(x["goto_links"] for x in counts)}


def release_preflight(*,docx:bytes,final_pdf:bytes,chapter_titles:list[str],
                      office_refreshed:bool=False,expected_pages:int|None=None,
                      allow_intentional_final_blank:bool=False)->dict:
    if not isinstance(chapter_titles,list) or not chapter_titles or len(chapter_titles)>200:
        raise ValueError("A bounded, nonempty chapter list is required")
    toc=inspect_docx_toc(docx)
    proof=inspect_pdf(final_pdf,chapter_titles=chapter_titles,
                      expected_pages=expected_pages)
    blocks=[]
    notes=[]
    def block(code,message):
        blocks.append({"code":code,"message":message})
    if not toc["toc_field"]:
        block("DOCX_TOC_FIELD_MISSING","DOCX does not contain a dynamic Word TOC field")
    if not toc["update_on_open"]:
        if office_refreshed:
            notes.append({"code":"UPDATE_FLAG_CLEARED_BY_OFFICE",
                          "message":"Office refreshed TOC; update-on-open flag is no longer set in refreshed copy"})
        else:
            block("DOCX_FIELDS_NOT_MARKED","DOCX fields are not marked for recalculation")
    if not office_refreshed:
        block("OFFICE_REFRESH_NOT_VERIFIED",
              "Word/LibreOffice refresh has not been performed and verified")
    if not proof["toc_found"]:
        block("PRINTED_TOC_NOT_FOUND","Rendered final PDF has no identifiable contents")
    if len(proof["headings"])!=len(chapter_titles):
        block("CHAPTER_COVERAGE","Not all requested chapters were checked")
    for row in proof["headings"]:
        if row["match"] is not True:
            block("TOC_MISMATCH_OR_UNKNOWN",
                  str(row["title"])+": TOC "+str(row["toc_page"])+
                  ", actual "+str(row["actual_pdf_page"]))
    for item in proof["findings"]:
        if item["code"] in ("TOC_PAGE_MISMATCH","PAGE_COUNT_MISMATCH",
                             "CHAPTER_NOT_FOUND","TOC_PAGE_UNKNOWN"):
            block(item["code"],item["message"])
        elif item["code"] in ("NEAR_EMPTY_PAGE","FINAL_EMPTY_PAGE"):
            if item["code"]=="FINAL_EMPTY_PAGE" and allow_intentional_final_blank:
                notes.append(item)
            else:
                block(item["code"],item["message"])
        else:
            notes.append(item)
    # Internal links are a separate quality gate: a correct visible number can
    # still point at the wrong target. At least require navigable TOC entries.
    with fitz.open(stream=final_pdf,filetype="pdf") as file:
        toc_pages=[idx+1 for idx in range(min(20,file.page_count))
                   if "содержание" in file.load_page(idx).get_text("text").casefold()
                   or "contents" in file.load_page(idx).get_text("text").casefold()]
    links=_toc_links(final_pdf,toc_pages)
    if links["total_links"]<len(chapter_titles):
        block("TOC_LINKS_MISSING",
              "Printed TOC has fewer internal PDF links than chapters")
    return {"status":"PASS" if not blocks else "BLOCKED",
            "publication_allowed":not blocks,
            "pdf_pages":proof["page_count"],
            "headings":proof["headings"],"toc_links":links,
            "docx_toc":toc,
            "office_refreshed_attested":bool(office_refreshed),
            "pdf_sha256":proof["pdf_sha256"],
            "blocking":blocks,"warnings":notes,
            "note":"A passing automated gate does not independently verify historical facts."}
