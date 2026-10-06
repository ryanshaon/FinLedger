"""File prep for Person 1: raw bytes -> raw_markdown + layout_blocks + page images.

Digital PDF  -> MarkItDown markdown (pdfminer) + pdfplumber layout (lines, tables with cells, bboxes).
Scanned page -> rendered PNG (no text layer). Mixed PDFs get both: text where it exists, images where it doesn't;
OCR of those regions is Person 1's vision path.
Photo        -> normalised PNG page (EXIF rotation applied).
XLSX / CSV   -> MarkItDown markdown tables.
"""
from __future__ import annotations

import functools
import io
from dataclasses import dataclass, field

import pdfplumber
from markitdown import MarkItDown, StreamInfo
from PIL import Image, ImageOps

from . import files
from .queue import PermanentError

MIN_PAGE_TEXT_CHARS = 16   # below this a page counts as scanned
RENDER_DPI = 200
MAX_IMAGE_SIDE = 2400


@dataclass
class Prepared:
    markdown: str
    layout: dict | None
    page_images: dict[int, bytes] = field(default_factory=dict)  # page_no (1-based) -> PNG
    page_count: int | None = None

    @property
    def is_scanned(self) -> bool:
        return not self.markdown.strip()


@functools.cache
def _markitdown() -> MarkItDown:
    return MarkItDown(enable_plugins=False)


def to_markdown(data: bytes, mime: str) -> str:
    info = StreamInfo(mimetype=mime, extension="." + files.EXT[mime])
    return _markitdown().convert_stream(io.BytesIO(data), stream_info=info).markdown or ""


def _png(img: Image.Image) -> bytes:
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def prep_pdf(data: bytes, page_cap: int) -> Prepared:
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as e:  # pdfminer raises a zoo of types for encrypted / broken files
        raise PermanentError(f"unreadable PDF: {type(e).__name__}") from e
    with pdf:
        page_count = len(pdf.pages)
        if page_count == 0:
            raise PermanentError("PDF has no pages")
        pages_meta, blocks, textless = [], [], []
        for n, page in enumerate(pdf.pages[:page_cap], start=1):
            try:
                text = page.extract_text() or ""
                lines = page.extract_text_lines()
                tables = page.find_tables()
            except Exception as e:
                raise PermanentError(f"unreadable PDF page {n}: {type(e).__name__}") from e
            has_text = len(text.strip()) >= MIN_PAGE_TEXT_CHARS
            pages_meta.append({"page": n, "width": float(page.width), "height": float(page.height), "has_text": has_text})
            if not has_text:
                textless.append(n)
                continue
            blocks += [{"page": n, "type": "line", "bbox": [round(l["x0"], 1), round(l["top"], 1), round(l["x1"], 1),
                                                           round(l["bottom"], 1)], "text": l["text"]} for l in lines]
            blocks += [{"page": n, "type": "table", "bbox": [round(v, 1) for v in t.bbox], "cells": t.extract()}
                       for t in tables]

    markdown = to_markdown(data, files.PDF) if len(textless) < min(page_count, page_cap) else ""
    images: dict[int, bytes] = {}
    if textless:
        import pypdfium2  # ships with pdfplumber

        doc = pypdfium2.PdfDocument(data)
        try:
            for n in textless:
                images[n] = _png(doc[n - 1].render(scale=RENDER_DPI / 72).to_pil())
        finally:
            doc.close()
    layout = {"pages": pages_meta, "blocks": blocks, "truncated": page_count > page_cap}
    return Prepared(markdown, layout, images, page_count)


def prep_image(data: bytes) -> Prepared:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise PermanentError(f"unreadable image: {type(e).__name__}") from e
    return Prepared("", None, {1: _png(img)}, 1)


def prepare(data: bytes, mime: str, page_cap: int = 30) -> Prepared:
    if mime == files.PDF:
        return prep_pdf(data, page_cap)
    if mime in (files.JPEG, files.PNG):
        return prep_image(data)
    if mime in (files.XLSX, files.CSV):
        try:
            return Prepared(to_markdown(data, mime), None)
        except Exception as e:
            raise PermanentError(f"unreadable spreadsheet: {type(e).__name__}") from e
    raise PermanentError(f"no prep for {mime}")
