"""PDF page rendering.

Mirage reads pixels, so a PDF is rasterised first. 200 DPI keeps body text
around 25 px tall, which is where the model reads most accurately.
"""
from __future__ import annotations

import io

import pymupdf as fitz  # PyMuPDF
from PIL import Image


def render_pdf(data: bytes, dpi: int = 200, max_pages: int = 50) -> list[tuple[int, Image.Image]]:
    """Return a list of (page_number, PIL image) for every page of the PDF."""
    pages = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for index, page in enumerate(doc):
            if index >= max_pages:
                break
            pixmap = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
            image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
            pages.append((index + 1, image))
    return pages
