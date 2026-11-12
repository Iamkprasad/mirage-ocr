"""Ground-truth samples for the OCR tests.

`python -m tests.samples` writes the files into tests/samples/ for manual use.
"""
from __future__ import annotations

import os

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    """A sans serif font from whatever the machine has, so the tests run anywhere."""
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)

DOCUMENT_LINES = [
    "ACME Industrial Supplies",
    "Invoice #A-2291",
    "Date: 2026-11-12",
    "Total amount: $1,480.00",
    "Ref: 88-31-B",
    "IBAN: GB29 NWBK 6016 1331 9268 19",
]

RECEIPT_LINES = [
    "BLUE BOTTLE COFFEE",
    "12/11/2026 09:41",
    "LATTE 5.20",
    "MUFFIN 4.50",
    "TOTAL 9.70",
    "VISA ****4417",
]


def make_document_image(dpi: int = 200) -> Image.Image:
    """A clean page of text, like a scanned invoice."""
    width, height = int(8.27 * dpi), int(11.69 * dpi)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = load_font(int(dpi / 12))
    for index, line in enumerate(DOCUMENT_LINES):
        draw.text((int(0.8 * dpi), int((1.2 + index * 0.7) * dpi)), line, fill="black", font=font)
    return image


def make_receipt_image(dpi: int = 150) -> Image.Image:
    """A receipt photo: skewed, uneven lighting, sensor noise, JPEG artefacts."""
    width, height = int(2.6 * dpi), int(4.2 * dpi)
    image = Image.new("RGB", (width, height), (238, 236, 230))
    draw = ImageDraw.Draw(image)
    font = load_font(int(dpi / 16))
    for index, line in enumerate(RECEIPT_LINES):
        draw.text((int(0.25 * dpi), int((0.4 + index * 0.42) * dpi)), line, fill=(25, 25, 30), font=font)

    # perspective-ish skew
    image = image.rotate(-3.5, expand=True, fillcolor=(238, 236, 230), resample=Image.BICUBIC)
    # uneven lighting: brighter at the top, darker towards the bottom
    gradient = Image.linear_gradient("L").resize(image.size)
    shade = gradient.point(lambda p: 255 - int(p * 0.35))
    image = Image.blend(image, Image.merge("RGB", (shade, shade, shade)), 0.35)
    image = image.filter(ImageFilter.GaussianBlur(0.4))
    return image


def make_pdf(pages: int = 2, dpi: int = 200) -> bytes:
    """A text PDF, the way a report generator would write one."""
    document = fitz.open()
    for page_number in range(pages):
        page = document.new_page(width=595, height=842)  # A4 in points
        font = fitz.Font("helv")
        y = 100
        for index, line in enumerate(DOCUMENT_LINES):
            page.insert_text((60, y), line, fontsize=13, fontname="helv")
            y += 34
        page.insert_text((60, y + 20), f"Page {page_number + 1} of {pages}", fontsize=11, fontname="helv")
    buffer = document.tobytes()
    document.close()
    return buffer


def make_document_photo(dpi: int = 300) -> Image.Image:
    """A page shot with a phone: perspective, uneven light, blur, JPEG artefacts."""
    width, height = int(8.27 * dpi), int(11.69 * dpi)
    page = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(page)
    font = load_font(int(dpi / 12))
    for index, line in enumerate(DOCUMENT_LINES):
        draw.text((int(0.8 * dpi), int((1.2 + index * 0.7) * dpi)), line, fill="black", font=font)

    quad = [120, 60, 30, height, width, height - 90, width - 40, 0]  # UL, LL, LR, UR
    photo = page.transform((width, height), Image.Transform.QUAD, quad, Image.BICUBIC, fillcolor="white")
    gradient = Image.linear_gradient("L").resize(photo.size).rotate(115)
    shade = gradient.point(lambda p: 255 - int(p * 0.45))
    photo = Image.blend(photo, Image.merge("RGB", (shade, shade, shade)), 0.4)
    return photo.filter(ImageFilter.GaussianBlur(1.1))


def save_all(directory: str) -> list[str]:
    os.makedirs(directory, exist_ok=True)
    paths = []
    document = make_document_image()
    path = os.path.join(directory, "document.png")
    document.save(path)
    paths.append(path)

    receipt = make_receipt_image()
    path = os.path.join(directory, "receipt.jpg")
    receipt.save(path, "JPEG", quality=75)
    paths.append(path)

    photo = make_document_photo()
    path = os.path.join(directory, "document_photo.jpg")
    photo.save(path, "JPEG", quality=72)
    paths.append(path)

    path = os.path.join(directory, "document.pdf")
    with open(path, "wb") as handle:
        handle.write(make_pdf())
    paths.append(path)
    return paths


if __name__ == "__main__":
    for saved in save_all(os.path.join(os.path.dirname(__file__), "samples")):
        print("wrote", saved)
