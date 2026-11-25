#!/usr/bin/env python3
"""Refresh Word TOC indexes with local LibreOffice UNO, then export DOCX + PDF.

Run with the system interpreter that has python3-uno:
    /usr/bin/python3 scripts/refresh_docx_toc_office.py INPUT.docx OUTPUT_DIR
No network services persist: private pipe + disposable LibreOffice profile.
This routine never overwrites the supplied input file.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
import shutil


def _prop(name,value):
    from com.sun.star.beans import PropertyValue
    prop=PropertyValue()
    prop.Name=name
    prop.Value=value
    return prop


def run(input_docx:Path,destination:Path,*,timeout:float=65.0):
    if input_docx.suffix.casefold()!=".docx" or not input_docx.is_file():
        raise ValueError("A real .docx is required")
    binary=shutil.which("libreoffice") or shutil.which("soffice")
    if not binary:
        raise RuntimeError("LibreOffice Writer executable is not installed")
    try:
        import uno
    except ImportError as exc:
        raise RuntimeError("Run with /usr/bin/python3 and package python3-uno") from exc
    destination.mkdir(parents=True,exist_ok=True)
    copied=destination/(input_docx.stem+"-toc-refreshed.docx")
    pdf=destination/(input_docx.stem+"-toc-refreshed.pdf")
    if copied.resolve()==input_docx.resolve():
        raise ValueError("Refusing to overwrite input DOCX")
    pipe="achewiw_toc_"+uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="achewiw-office-") as tmp:
        profile=Path(tmp)/"profile"
        cmd=[binary,"-env:UserInstallation="+profile.as_uri(),"--headless",
             "--norestore","--nodefault","--nofirststartwizard",
             "--accept=pipe,name="+pipe+";urp;StarOffice.ServiceManager"]
        proc=subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        doc=None
        try:
            local=uno.getComponentContext()
            resolver=local.ServiceManager.createInstanceWithContext(
                "com.sun.star.bridge.UnoUrlResolver",local)
            context=None
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                if proc.poll() is not None:
                    raise RuntimeError("LibreOffice exited before UNO handshake")
                try:
                    context=resolver.resolve(
                        "uno:pipe,name="+pipe+";urp;StarOffice.ComponentContext")
                    break
                except Exception:
                    time.sleep(0.3)
            if context is None:
                raise TimeoutError("LibreOffice UNO did not start before timeout")
            desktop=context.ServiceManager.createInstanceWithContext(
                "com.sun.star.frame.Desktop",context)
            doc=desktop.loadComponentFromURL(
                input_docx.resolve().as_uri(),"_blank",0,
                (_prop("Hidden",True),_prop("ReadOnly",False),
                 _prop("UpdateDocMode",3),_prop("MacroExecutionMode",4)))
            if doc is None:
                raise RuntimeError("LibreOffice cannot open the DOCX")
            indexes=doc.getDocumentIndexes()
            count=indexes.getCount()
            if count<1:
                raise RuntimeError("No dynamic TOC/index imported by LibreOffice")
            for index in range(count):
                indexes.getByIndex(index).update()
            doc.refresh()
            doc.storeAsURL(copied.resolve().as_uri(),(
                _prop("FilterName","Office Open XML Text"),
                _prop("Overwrite",True)))
            doc.storeToURL(pdf.resolve().as_uri(),(
                _prop("FilterName","writer_pdf_Export"),
                _prop("Overwrite",True)))
            if not copied.is_file() or not pdf.is_file():
                raise RuntimeError("Office did not write both outputs")
            return {"original":str(input_docx),"refreshed_docx":str(copied),
                "refreshed_pdf":str(pdf),"index_count":count,
                "office_updated":True}
        finally:
            if doc is not None:
                try:doc.close(True)
                except Exception:pass
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=7)
                except subprocess.TimeoutExpired:proc.kill()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("docx",type=Path)
    parser.add_argument("output_dir",type=Path)
    opts=parser.parse_args()
    print(json.dumps(run(opts.docx,opts.output_dir),ensure_ascii=False))
