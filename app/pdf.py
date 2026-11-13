"""PDF page rendering and text reading.

Mirage reads pixels, so a PDF is rasterised first. 200 DPI keeps body text
around 25 px tall, which is where the model reads most accurately.

PDFium (through pypdfium2) does the rasterising: BSD-3-Clause and Apache-2.0,
so the whole stack stays permissive.

A PDF written by a word processor also carries the text it was written with.
That text is exact, it comes with its own geometry, and it costs nothing, so
it is worth reading when it is there.
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


def read_text_layer(data: bytes, dpi: int = 200, max_pages: int = 50) -> list[list[dict]]:
    """The text a PDF already carries, page by page, with boxes in pixels.

    Returns an empty list for a page that carries no text, which is how a
    scanned document is recognised.
    """
    pages = []
    document = pdfium.PdfDocument(data)
    try:
        for index in range(min(len(document), max_pages)):
            pages.append(_page_text(document[index], dpi))
    finally:
        document.close()
    return pages


def _page_text(page, dpi: int) -> list[dict]:
    """The text of one page, as the PDF wrote it, with boxes in pixels.

    The characters are grouped into rows by where they sit on the page, but
    the text of a row is the PDF's own text, spaces included, so nothing is
    guessed.
    """
    textpage = page.get_textpage()
    try:
        count = textpage.count_chars()
        if not count:
            return []
        scale = dpi / 72
        height_page = page.get_height()
        rows: list[dict] = []
        last_row = None
        for index in range(count):
            char = textpage.get_text_range(index, 1)
            box = textpage.get_charbox(index)
            if box is None:
                continue
            left, bottom, right, top = box  # PDFium returns left, bottom, right, top
            if not char.strip():
                if last_row is not None:  # a space belongs to the row it follows
                    last_row["chars"].append((left, right, bottom, top, char))
                continue
            char_height = max(0.1, top - bottom)
            for row in rows:
                overlap = min(row["top"], top) - max(row["bottom"], bottom)
                if overlap >= 0.4 * char_height:
                    row["chars"].append((left, right, bottom, top, char))
                    row["top"] = max(row["top"], top)
                    row["bottom"] = min(row["bottom"], bottom)
                    last_row = row
                    break
            else:
                row = {"top": top, "bottom": bottom,
                       "chars": [(left, right, bottom, top, char)]}
                rows.append(row)
                last_row = row

        lines = []
        for row in rows:
            text = "".join(char for _, _, _, _, char in row["chars"]).strip()
            ink = [c for c in row["chars"] if c[4].strip()]
            if not ink or not text:
                continue
            left = min(c[0] for c in ink)
            right = max(c[1] for c in ink)
            bottom = min(c[2] for c in ink)
            top = max(c[3] for c in ink)
            lines.append(
                {
                    "text": text,
                    "bbox": [
                        int(left * scale),
                        int((height_page - top) * scale),
                        int(right * scale),
                        int((height_page - bottom) * scale),
                    ],
                    "score": 1.0,
                }
            )
        return lines
    finally:
        textpage.close()
