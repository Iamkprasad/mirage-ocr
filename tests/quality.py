"""Quality report: character error rate per sample."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image
from app.engine import OcrEngine
from app.pdf import render_pdf
from tests.samples import (DOCUMENT_LINES, RECEIPT_LINES, make_document_image, make_document_photo,
                           make_pdf, make_receipt_image)
from tests.test_ocr import best_error_rate, character_error_rate

engine = OcrEngine(tile_size=int(sys.argv[1]) if len(sys.argv) > 1 else 896)

samples = [
    ("document.png (200 dpi scan)", make_document_image(), DOCUMENT_LINES),
    ("document_photo.jpg (phone photo)", make_document_photo(), DOCUMENT_LINES),
    ("receipt.jpg (small photo)", make_receipt_image(), RECEIPT_LINES),
]
for number, image in render_pdf(make_pdf(pages=2), dpi=200):
    samples.append((f"document.pdf page {number}", image, DOCUMENT_LINES))

for name, image, truth in samples:
    started = time.time()
    result = engine.ocr_image(image)
    errors = [best_error_rate(result.lines, line) for line in truth]
    print(f"\n=== {name}  {result.width}x{result.height}  {time.time()-started:.1f}s")
    for line, error in zip(truth, errors):
        mark = "ok " if error <= 0.05 else "MISS"
        best = min(result.lines, key=lambda l: character_error_rate(line, l.text), default=None)
        print(f"  {mark} cer={error:.3f} {line!r}")
        if best and error > 0.05:
            print(f"        got {best.text!r}")
    print(f"  lines detected: {len(result.lines)}")
    print(f"  mean CER: {sum(errors)/len(errors):.3f}")
