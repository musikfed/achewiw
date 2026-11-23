"""R3.5 EXPERIMENTAL: conservative, opt-in tracing of simple raster diagrams.

Not a general vector converter. Potrace outlines quantized raster colors;
text becomes approximate paths (NOT editable text), photos may look worse.
This module NEVER writes to the source DOCX and never inserts the SVG
automatically into a book. One request at a time, small input + output
limits, no LLM, GPU, remote APIs or external resources.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore
from xml.etree import ElementTree as ET
import shutil
import subprocess

import fitz
from PIL import Image, ImageOps, UnidentifiedImageError
from docx import Document

from docwork_image_layout import MAX_IMAGES, _picture_entry

SVG_NS = "http://www.w3.org/2000/svg"
MAX_VECTOR_PIXELS = 1_300_000
MAX_VECTOR_BYTES = 1_800_000
MAX_TRACE_LAYERS = 6
_TRACE_SLOT = BoundedSemaphore(1)


class VectorExperimentError(ValueError):
    pass


def source_image(docx_bytes: bytes, index: int) -> bytes:
    if type(index) is not int or index < 0 or index >= MAX_IMAGES:
        raise VectorExperimentError("Недопустимый номер иллюстрации")
    doc = Document(BytesIO(docx_bytes))
    if index >= len(doc.inline_shapes):
        raise VectorExperimentError("Иллюстрация не найдена в DOCX")
    info, raw = _picture_entry(doc, index, doc.inline_shapes[index])
    if raw is None:
        raise VectorExperimentError(info.get("reason", "Невозможно прочитать изображение"))
    return raw


def _trace_mask(mask: Image.Image) -> ET.Element | None:
    """Execute local Potrace on a bounded PBM; return its XML <g> element."""
    pbm = BytesIO()
    mask.save(pbm, format="PPM")  # PIL mode '1' writes portable PBM (P4).
    with TemporaryDirectory(prefix="achewiw-vector-") as tmp:
        source = Path(tmp) / "figure.pbm"
        output = Path(tmp) / "figure.svg"
        source.write_bytes(pbm.getvalue())
        try:
            subprocess.run(
                ["potrace", "-b", "svg", "--turdsize", "2", "--opttolerance", "0.25",
                 "-o", str(output), str(source)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=7,
            )
        except FileNotFoundError as exc:
            raise VectorExperimentError(
                "Для трассировки на сервере отсутствует Potrace"
            ) from exc
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise VectorExperimentError(
                "Трассировка схемы завершилась ошибкой или превышением лимита времени"
            ) from exc
        if not output.is_file() or output.stat().st_size > MAX_VECTOR_BYTES:
            raise VectorExperimentError("Слишком большой SVG после трассировки")
        if output.stat().st_size < 60:
            return None
        try:
            root = ET.fromstring(output.read_bytes())
        except ET.ParseError as exc:
            raise VectorExperimentError("Potrace вернул некорректный SVG") from exc
        return root.find(f"{{{SVG_NS}}}g")


def raster_to_svg(raw: bytes, colors: int = 4) -> bytes:
    """Quantize to <=6 flat colors, trace as paths, and keep source untouched."""
    if type(colors) is not int or colors not in (2, 4, 6):
        raise VectorExperimentError("Цветов для схемы должно быть 2, 4 или 6")
    if shutil.which("potrace") is None:
        raise VectorExperimentError("Potrace пока не установлен на хосте")
    if not _TRACE_SLOT.acquire(blocking=False):
        raise VectorExperimentError("Уже идёт трассировка другой схемы — повтори позже")
    try:
        try:
            with Image.open(BytesIO(raw)) as image:
                image.load()
                w,h=image.size
                if w*h>MAX_VECTOR_PIXELS or w<10 or h<10:
                    raise VectorExperimentError(
                        "Трассировка разрешена для схем размером от 10 пикселей, "
                        "общая площадь не более 1,3 млн пикселей"
                    )
                base = Image.new("RGB",(w,h),"white")
                if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                    base.paste(image.convert("RGBA"), mask=image.convert("RGBA").getchannel("A"))
                else:
                    base.paste(image.convert("RGB"))
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise VectorExperimentError("Изображение нельзя прочитать") from exc

        indexed = base.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
        palette = indexed.getpalette()
        count = indexed.getcolors(maxcolors=256)
        if not count:
            raise VectorExperimentError("Нет областей для трассировки")
        # A raster with thousands of changing colors (photo/gradient) is
        # quantized, not honestly reconstructed; UI always labels as experimental.
        indices=list(indexed.getdata())
        bg_idx=max(count, key=lambda entry:entry[0])[1]
        def color_at(i):
            rgb=palette[i*3:i*3+3]
            return "#%02x%02x%02x" % tuple(rgb)
        svg=ET.Element(f"{{{SVG_NS}}}svg",{
            "width":str(w), "height":str(h),
            "viewBox":f"0 0 {w} {h}",
            "version":"1.1",
        })
        ET.SubElement(svg,f"{{{SVG_NS}}}rect",{
            "width":str(w),"height":str(h),"fill":color_at(bg_idx),
        })
        for count_px,idx in sorted(count,key=lambda item:item[0],reverse=True):
            if idx==bg_idx or count_px<=2:
                continue
            if len(svg)>=MAX_TRACE_LAYERS+2:
                break
            bitmap=Image.new("1",(w,h),1)
            bitmap.putdata([0 if pixel==idx else 1 for pixel in indices])
            group=_trace_mask(bitmap)
            if group is None:
                continue
            group.set("fill",color_at(idx))
            svg.append(group)
        ET.register_namespace("",SVG_NS)
        result=ET.tostring(svg,encoding="utf-8",xml_declaration=True)
        if len(result)>MAX_VECTOR_BYTES:
            raise VectorExperimentError("SVG слишком сложный. Уменьши количество цветов")
        return result
    finally:
        _TRACE_SLOT.release()


def svg_to_png(svg: bytes, scale: int = 2) -> bytes:
    """Rasterize ONLY our just-created trusted Potrace SVG, max 4x/4M pixels."""
    if type(scale) is not int or scale not in (1,2,3,4):
        raise VectorExperimentError("Масштаб PNG может быть только 1×–4×")
    try:
        with fitz.open(stream=svg,filetype="svg") as document:
            page=document[0]
            w,h=page.rect.width,page.rect.height
            if w*h*scale*scale>5_000_000:
                raise VectorExperimentError("Итоговый PNG превышает безопасный лимит")
            pixmap=page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
            png=pixmap.tobytes("png")
            if len(png)>5_000_000:
                raise VectorExperimentError("Результат PNG слишком велик")
            return png
    except (RuntimeError, ValueError) as exc:
        if isinstance(exc,VectorExperimentError):
            raise
        raise VectorExperimentError("Не удалось визуализировать SVG как PNG") from exc


def convert_picture(docx_bytes: bytes, index: int,
                    colors: int = 4, output: str = "svg", scale: int = 2) -> bytes:
    if output not in ("svg","png"):
        raise VectorExperimentError("Можно экспортировать только SVG или PNG")
    raw=source_image(docx_bytes,index)
    svg=raster_to_svg(raw,colors)
    return svg if output=="svg" else svg_to_png(svg,scale)
