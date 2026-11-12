"""Mirage-backed OCR for images and rendered PDF pages.

Mirage is a 512 px vision model: it reads text from a fixed 16x16 grid of 32 px
cells, so a whole A4 page handed over at once is downscaled until the glyphs
are a few pixels tall and the characters start to turn into each other. This
module therefore cuts the page into overlapping square windows, reads every
window, merges the readings back into page coordinates, and re-reads any line
that a window edge cut in half.
"""
from __future__ import annotations

import difflib
import threading
import time
from dataclasses import dataclass, field

import cactus
from PIL import Image, ImageChops, ImageFilter, ImageOps

from . import parallel
from .log import LOG

BLANK_CONTRAST = 10  # a window whose sharpest detail is below this holds no ink


@dataclass
class Line:
    text: str
    bbox: tuple[int, int, int, int]  # pixel coordinates in page space
    score: float
    clipped: bool = False

    def as_dict(self) -> dict:
        return {"text": self.text, "bbox": list(self.bbox), "score": round(self.score, 4)}


@dataclass
class PageResult:
    width: int
    height: int
    lines: list[Line] = field(default_factory=list)
    ms: int = 0
    timing: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "ms": self.ms,
            "timing": self.timing,
            "lines": [line.as_dict() for line in self.lines],
            "text": "\n".join(line.text for line in self.lines),
        }


def _overlap_ratio(a: tuple, b: tuple) -> float:
    """Fraction of the smaller box that sits inside the larger one."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    inter = max(0, min(ax1, bx1) - max(ax0, bx0)) * max(0, min(ay1, by1) - max(ay0, by0))
    if not inter:
        return 0.0
    area_a = max(1, (ax1 - ax0) * (ay1 - ay0))
    area_b = max(1, (bx1 - bx0) * (by1 - by0))
    return inter / min(area_a, area_b)


def _iou(a: tuple, b: tuple) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    inter = max(0, min(ax1, bx1) - max(ax0, bx0)) * max(0, min(ay1, by1) - max(ay0, by0))
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / union if union > 0 else 0.0


def _same_row(a: Line, b: Line) -> bool:
    top, bottom = max(a.bbox[1], b.bbox[1]), min(a.bbox[3], b.bbox[3])
    height = min(a.bbox[3] - a.bbox[1], b.bbox[3] - b.bbox[1])
    return height > 0 and (bottom - top) >= 0.5 * height


def _same_text(a: Line, b: Line) -> bool:
    if a.text in b.text or b.text in a.text:
        return True
    return difflib.SequenceMatcher(None, a.text, b.text).ratio() > 0.75


def merge_lines(candidates: list[Line]) -> list[Line]:
    """Collapse the readings of one physical line into a single entry."""
    kept: list[Line] = []
    for cand in sorted(candidates, key=lambda c: (-len(c.text), -c.score)):
        target = None
        for other in kept:
            if _same_row(cand, other) and _overlap_ratio(cand.bbox, other.bbox) >= 0.3:
                target = other
                break
            if _same_text(cand, other) and (
                _overlap_ratio(cand.bbox, other.bbox) >= 0.6 or _iou(cand.bbox, other.bbox) >= 0.3
            ):
                target = other
                break
        if target is None:
            kept.append(cand)
            continue
        _absorb(target, cand)
    return _reading_order(_merge_cut_lines(kept))


def _absorb(target: Line, cand: Line) -> None:
    target.bbox = (
        min(target.bbox[0], cand.bbox[0]),
        min(target.bbox[1], cand.bbox[1]),
        max(target.bbox[2], cand.bbox[2]),
        max(target.bbox[3], cand.bbox[3]),
    )
    target.clipped = target.clipped or cand.clipped
    target.score = max(target.score, cand.score)
    if len(cand.text) > len(target.text):
        target.text = cand.text


def _merge_cut_lines(lines: list[Line]) -> list[Line]:
    """Join fragments that a window edge cut apart."""
    merged: list[Line] = []
    for line in sorted(lines, key=lambda c: (c.bbox[1], c.bbox[0])):
        for other in merged:
            if not (line.clipped or other.clipped):
                continue
            if not _same_row(line, other):
                continue
            left, right = (other, line) if other.bbox[0] <= line.bbox[0] else (line, other)
            gap = right.bbox[0] - left.bbox[2]
            height = min(left.bbox[3] - left.bbox[1], right.bbox[3] - right.bbox[1])
            if gap <= 0.8 * height:
                _absorb(other, line)
                break
        else:
            merged.append(line)
    return merged


def _reading_order(lines: list[Line]) -> list[Line]:
    """Sort into reading order: rows by vertical overlap, then left to right."""
    rows: list[list[Line]] = []
    for line in sorted(lines, key=lambda c: (c.bbox[1], c.bbox[0])):
        for row in rows:
            top = min(l.bbox[1] for l in row)
            bottom = max(l.bbox[3] for l in row)
            overlap = min(bottom, line.bbox[3]) - max(top, line.bbox[1])
            if overlap > 0.4 * min(bottom - top, line.bbox[3] - line.bbox[1]):
                row.append(line)
                break
        else:
            rows.append([line])
    ordered: list[Line] = []
    for row in sorted(rows, key=lambda r: min(l.bbox[1] for l in r)):
        ordered.extend(sorted(row, key=lambda l: l.bbox[0]))
    return ordered


def _looks_blank(tile) -> bool:
    """True when a window holds no ink, so the model does not need to read it.

    The test is a high pass filter: a window is empty when it differs from a
    blurred copy of itself by almost nothing. Smooth shading, the grey corner
    of a photograph, is empty; a line of text is not.
    """
    small = tile.convert("L").resize((64, 64), Image.BILINEAR)
    blurred = small.filter(ImageFilter.GaussianBlur(2))
    difference = ImageChops.difference(small, blurred)
    return difference.getextrema()[1] < BLANK_CONTRAST


class OcrEngine:
    """Thin wrapper around one shared Mirage instance."""

    def __init__(self, tile_size: int = 896, overlap: float = 0.2, text_conf: float = 0.3,
                 max_dim: int = 2500, refine: bool = True, workers: int | None = None):
        self.tile_size = tile_size
        self.overlap = overlap
        self.text_conf = text_conf
        self.max_dim = max_dim
        self.refine = refine
        self.workers = workers
        self._lock = threading.Lock()
        self._model: cactus.Mirage | None = None

    @property
    def model(self) -> cactus.Mirage:
        with self._lock:
            if self._model is None:
                self._model = cactus.Mirage()
            return self._model

    def _tiles(self, width: int, height: int) -> list[tuple[int, int, int, int]]:
        size = self.tile_size
        step = max(1, int(size * (1 - self.overlap)))
        boxes = []
        for y in range(0, max(1, height), step):
            for x in range(0, max(1, width), step):
                box = (x, y, min(x + size, width), min(y + size, height))
                if box[2] - box[0] < 8 or box[3] - box[1] < 8:
                    continue
                boxes.append(box)
                if x + size >= width:
                    break
            if y + size >= height:
                break
        return boxes

    def _read_tile(self, crop) -> list[dict]:
        result = self.model.describe(
            crop, sections=["text"], fields=["bbox", "score"], text_conf=self.text_conf
        )
        return result.get("text", [])

    def _read_windows(self, rgb, tiles):
        """Yield (index, box, readings, ms, skipped) for every window of the page.

        Windows with no ink are skipped without a model call. The rest are read
        one after another, or by a pool of worker processes.
        """
        plan = []
        jobs = []
        for index, box in enumerate(tiles, start=1):
            crop = rgb.crop(box)
            if _looks_blank(crop):
                plan.append((index, box, None))
            else:
                plan.append((index, box, crop))
                jobs.append((index, box, crop))
        if not jobs:
            yield from ((index, box, [], 0, True) for index, box, _ in plan)
            return

        workers = self.workers if self.workers is not None else parallel.worker_count()
        results = {}
        if workers > 1 and len(jobs) > 1 and parallel.existing() is not None:
            try:
                results = self._read_parallel(jobs)
            except Exception as exc:  # a worker died, fall back to one process
                LOG.warning("parallel read failed (%s), reading in one process", exc)
                results = {}

        for index, box, crop in plan:
            if crop is None:
                yield index, box, [], 0, True
                continue
            if index in results:
                readings, elapsed = results[index]
            else:
                started = time.perf_counter()
                readings = self._read_tile(crop)
                elapsed = int((time.perf_counter() - started) * 1000)
            yield index, box, readings, elapsed, False

    def _read_parallel(self, jobs):
        """Read the windows in worker processes, one call per window."""
        executor = parallel.existing()
        if executor is None:
            raise RuntimeError("the reader pool is not started")
        submitted = time.perf_counter()
        futures = {}
        for index, box, crop in jobs:
            payload = (box, crop.tobytes(), crop.width, crop.height, self.text_conf)
            futures[index] = executor.submit(parallel._read, payload)
        results = {}
        for index, future in futures.items():
            box, readings = future.result()
            results[index] = (readings, int((time.perf_counter() - submitted) * 1000))
        return results

    def _refine(self, image, line: Line) -> str | None:
        """Re-read one line on its own, on a square canvas so it is not distorted."""
        x0, y0, x1, y1 = line.bbox
        pad = max(12, int(0.8 * (y1 - y0)))
        crop = image.crop(
            (
                max(0, x0 - pad),
                max(0, y0 - pad),
                min(image.width, x1 + pad),
                min(image.height, y1 + pad),
            )
        )
        if not crop.width or not crop.height:
            return None
        side = max(crop.size)
        canvas = Image.new("RGB", (side, side), crop.getpixel((0, 0)))
        canvas.paste(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
        readings = self._read_tile(canvas)
        if not readings:
            return None
        best = max(readings, key=lambda item: len(item.get("string") or ""))
        return (best.get("string") or "").strip() or None

    def ocr_image(self, image, progress=None) -> PageResult:
        """Read one RGB image and return its lines in page coordinates.

        `progress` is called with a dict after every window, so the caller can
        report what is happening while the page is still being read.
        """
        def report(event: dict) -> None:
            if progress:
                progress(event)

        rgb = ImageOps.exif_transpose(image).convert("RGB")
        scaled = False
        if max(rgb.size) > self.max_dim:
            scale = self.max_dim / max(rgb.size)
            rgb = rgb.resize((int(rgb.width * scale), int(rgb.height * scale)), Image.LANCZOS)
            scaled = True
        width, height = rgb.size
        started = time.perf_counter()

        load_started = time.perf_counter()
        _ = self.model
        report({"stage": "model", "ms": int((time.perf_counter() - load_started) * 1000)})

        tiles = self._tiles(width, height)
        report({"stage": "windows", "total": len(tiles), "width": width, "height": height,
                "scaled": scaled})

        candidates: list[Line] = []
        read_ms = 0
        skipped = 0
        for index, box, readings, window_ms, was_skipped in self._read_windows(rgb, tiles):
            tw = box[2] - box[0]
            th = box[3] - box[1]
            if was_skipped:
                skipped += 1
                report({"stage": "window", "index": index, "total": len(tiles), "ms": 0,
                        "skipped": True, "readings": 0, "lines": [],
                        "width": width, "height": height})
                continue
            for item in readings:
                text = (item.get("string") or "").strip()
                if not text:
                    continue
                x0, y0, x1, y1 = item["bbox"]
                line = Line(
                    text=text,
                    bbox=(
                        box[0] + int(x0 / 1000 * tw),
                        box[1] + int(y0 / 1000 * th),
                        box[0] + int(x1 / 1000 * tw),
                        box[1] + int(y1 / 1000 * th),
                    ),
                    score=float(item.get("score", 0.0)),
                )
                line.clipped = self._is_clipped(line, box, width, height)
                candidates.append(line)
            read_ms += window_ms
            current = merge_lines(candidates)
            report({"stage": "window", "index": index, "total": len(tiles), "ms": window_ms,
                    "skipped": False, "readings": len(readings),
                    "width": width, "height": height,
                    "lines": [line.as_dict() for line in current]})

        merge_started = time.perf_counter()
        lines = merge_lines(candidates)
        merge_ms = int((time.perf_counter() - merge_started) * 1000)

        refine_ms = 0
        for line in lines:
            if not line.clipped or not self.refine:
                continue
            refine_started = time.perf_counter()
            refined = self._refine(rgb, line)
            refine_ms += int((time.perf_counter() - refine_started) * 1000)
            if refined and len(refined) >= 0.9 * len(line.text):
                line.text = refined

        elapsed = int((time.perf_counter() - started) * 1000)
        timing = {
            "windows": len(tiles),
            "windows_skipped": skipped,
            "read_ms": read_ms,
            "merge_ms": merge_ms,
            "refine_ms": refine_ms,
            "lines": len(lines),
            "total_ms": elapsed,
        }
        report({"stage": "page_done", "ms": elapsed, "lines": len(lines), "timing": timing})
        return PageResult(width=width, height=height, lines=lines, ms=elapsed, timing=timing)

    @staticmethod
    def _is_clipped(line: Line, tile: tuple, width: int, height: int) -> bool:
        """True when the line runs into a window edge that is not a page edge."""
        tx0, ty0, tx1, ty1 = tile
        bx0, by0, bx1, by1 = line.bbox
        tolerance = 8
        return (
            (tx0 > 0 and bx0 <= tx0 + tolerance)
            or (tx1 < width and bx1 >= tx1 - tolerance)
            or (ty0 > 0 and by0 <= ty0 + tolerance)
            or (ty1 < height and by1 >= ty1 - tolerance)
        )
