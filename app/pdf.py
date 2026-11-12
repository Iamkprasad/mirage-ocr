"""PDF page rendering.

Mirage reads pixels, so a PDF is rasterised first. 200 DPI keeps body text
around 25 px tall, which is where the model reads most accurately.

PDFium (through pypdfium2) does the rasterising: BSD-3-Clause and Apache-2.0,
so the whole stack stays permissive.
"""
from __future__ import annotations

import pypdfium2 as pdfium
from PIL import Image


def render_pdf(data: bytes, dpi: int = 200, max_pages: int = 50) -> list[tuple[int, Image.Image]]:
    """Return a list of (page_number, PIL image) for every page of the PDF."""
    pages = []
    document = pdfium.PdfDocument(data)
    try:
        for index in range(min(len(document), max_pages)):
            page = document[index]
            bitmap = page.render(scale=dpi / 72)
            image = bitmap.to_pil().convert("RGB")
            pages.append((index + 1, image))
    finally:
        document.close()
    return pages
