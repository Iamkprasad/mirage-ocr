"""Measure the OCR accuracy of a digital PDF against its own text layer.

A PDF produced by a word processor carries the text it was written with, so
the OCR output can be compared with the truth, page by page.

Usage:
    python tests/eval_textlayer.py file.pdf [--dpi 200] [--tile 896] [--overlap 0.2]
"""
from __future__ import annotations

import argparse
import difflib
from collections import Counter
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pypdfium2 as pdfium

from app.engine import OcrEngine


def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def edit_distance(reference: str, candidate: str) -> int:
    matcher = difflib.SequenceMatcher(None, reference, candidate)
    return sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    )


def page_ground_truth(pdf, index: int) -> str:
    page = pdf[index]
    return page.get_textpage().get_text_range()


def evaluate(path: str, dpi: int, tile: int, overlap: float, text_conf: float, refine: bool,
             limit: int = 0, show_missing: int = 0, max_dim: int = 2500) -> dict:
    pdf = pdfium.PdfDocument(path)
    pages = list(range(len(pdf)))
    if limit:
        pages = pages[:limit]
    engine = OcrEngine(tile_size=tile, overlap=overlap, text_conf=text_conf, refine=refine,
                        max_dim=max_dim)

    totals = {"pages": 0, "gt_chars": 0, "errors": 0, "words": 0, "words_found": 0,
              "ocr_words": 0, "ocr_words_found": 0, "read_ms": 0, "windows": 0,
              "skipped": 0, "lines": 0, "chars_shared": 0}
    missing_words = []
    for index in pages:
        truth = page_ground_truth(pdf, index)
        result = engine.ocr_image(render_page(pdf, index, dpi))
        timing = result.timing
        ocr = "\n".join(line.text for line in result.lines)

        reference, candidate = normalise(truth), normalise(ocr)
        errors = edit_distance(reference, candidate)
        truth_words = re.findall(r"[A-Za-z0-9]+", truth.lower())
        ocr_words = re.findall(r"[A-Za-z0-9]+", ocr.lower())
        truth_set, ocr_set = set(truth_words), set(ocr_words)
        found = sum(1 for word in truth_words if word in ocr_set)
        matched = sum(1 for word in ocr_words if word in truth_set)
        # characters, ignoring order: how many of the truth characters appear
        truth_bag, ocr_bag = Counter(reference), Counter(candidate)
        shared = sum((truth_bag & ocr_bag).values())
        if show_missing:
            missing_words += [w for w in truth_words if w not in ocr_set]

        totals["pages"] += 1
        totals["gt_chars"] += len(reference)
        totals["errors"] += errors
        totals["words"] += len(truth_words)
        totals["words_found"] += found
        totals["ocr_words"] += len(ocr_words)
        totals["ocr_words_found"] += matched
        totals["read_ms"] += timing["read_ms"]
        totals["windows"] += timing["windows"]
        totals["skipped"] += timing["windows_skipped"]
        totals["lines"] += timing["lines"]
        totals["chars_shared"] += shared
        print(
            f"  page {index + 1}: recall={found / max(1, len(truth_words)):.4f} "
            f"precision={matched / max(1, len(ocr_words)):.4f} "
            f"chars={shared / max(1, len(reference)):.4f} "
            f"lines={timing['lines']} ms={timing['total_ms']} "
            f"windows={timing['windows'] - timing['windows_skipped']}",
            flush=True,
        )
    totals["cer"] = totals["errors"] / max(1, totals["gt_chars"])
    totals["word_recall"] = totals["words_found"] / max(1, totals["words"])
    totals["word_precision"] = totals["ocr_words_found"] / max(1, totals["ocr_words"])
    totals["char_accuracy"] = totals["chars_shared"] / max(1, totals["gt_chars"])
    totals["ms_per_page"] = totals["read_ms"] / max(1, totals["pages"])
    totals["missing_words"] = missing_words
    return totals


def render_page(pdf, index: int, dpi: int):
    page = pdf[index]
    return page.render(scale=dpi / 72).to_pil().convert("RGB")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--tile", type=int, default=896)
    parser.add_argument("--overlap", type=float, default=0.2)
    parser.add_argument("--text-conf", type=float, default=0.2)
    parser.add_argument("--no-refine", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--show-missing", type=int, default=0)
    parser.add_argument("--max-dim", type=int, default=2500)
    args = parser.parse_args()

    print(f"{args.pdf}  dpi={args.dpi} tile={args.tile} overlap={args.overlap} "
          f"text_conf={args.text_conf} refine={not args.no_refine}")
    totals = evaluate(args.pdf, args.dpi, args.tile, args.overlap, args.text_conf,
                       not args.no_refine, args.limit, args.show_missing, args.max_dim)
    print(
        f"\nword recall {totals['word_recall']:.4f} | word precision {totals['word_precision']:.4f} | "
        f"char accuracy {totals['char_accuracy']:.4f} | "
        f"{totals['ms_per_page'] / 1000:.1f}s per page | "
        f"{totals['windows'] - totals['skipped']} windows read, {totals['skipped']} skipped | "
        f"{totals['lines']} lines"
    )
    if args.show_missing:
        

        common = Counter(totals["missing_words"]).most_common(25)
        print("most often missed words:", common)


if __name__ == "__main__":
    main()
