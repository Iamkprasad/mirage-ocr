"""Mirage OCR web app: upload an image or a PDF, get the text back with boxes.

Two ways in:

* `POST /api/ocr`          one JSON answer, for scripts
* `POST /api/ocr/stream`   server sent events, so the browser can show what is
                            happening while the page is still being read
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError

from .engine import OcrEngine
from .log import LOG, recent, setup
from .metrics import MONITOR
from .pdf import read_text_layer, render_pdf
from . import parallel

setup()
LOG.info("mirage ocr starting, tile=%s", os.environ.get("OCR_TILE_SIZE", "896"))

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


class Cancelled(Exception):
    """Raised inside a job when the browser goes away."""


def start_readers() -> None:
    """Start the reader processes before the first request.

    They are forked now, while no inference is running, so they can never
    inherit a held engine lock.
    """
    if parallel.worker_count() > 1 and parallel.pool() is not None:
        LOG.info("started %d reader processes", parallel.worker_count())


@asynccontextmanager
async def lifespan(_: FastAPI):
    start_readers()
    yield
    parallel.shutdown()


app = FastAPI(title="Mirage OCR", description="Image and PDF OCR powered by Cactus Compute Mirage",
               lifespan=lifespan)

engine = OcrEngine(
    tile_size=int(os.environ.get("OCR_TILE_SIZE", 896)),
    overlap=float(os.environ.get("OCR_OVERLAP", 0.2)),
    text_conf=float(os.environ.get("OCR_TEXT_CONF", 0.2)),
    refine=os.environ.get("OCR_REFINE", "1") != "0",
)
PDF_DPI = int(os.environ.get("OCR_PDF_DPI", 200))


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "tile_size": engine.tile_size, "pdf_dpi": PDF_DPI}


@app.get("/api/metrics")
def metrics() -> dict:
    return MONITOR.snapshot()


@app.get("/api/logs")
def logs(limit: int = 200) -> dict:
    return {"entries": recent(limit)}


def preview_data_url(image) -> str:
    """A small JPEG of a page, so the browser can draw the boxes on top."""
    preview = image.copy()
    preview.thumbnail((1400, 2000))
    buffer = io.BytesIO()
    preview.save(buffer, "JPEG", quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def run_ocr(data: bytes, filename: str, dpi: int, progress=None, mode: str = "auto") -> dict:
    """The whole job: decode, read every page, collect the answer.

    A PDF page that carries its own text is read from that text, which is
    exact and instant. A page without one is a picture, and is read by the
    model.
    """
    started = time.perf_counter()
    is_pdf = data[:5] == b"%PDF-" or filename.lower().endswith(".pdf")
    pages = []
    windows = skipped = lines = 0
    read_ms = render_ms = 0

    def report(event: dict) -> None:
        if progress:
            progress(event)

    if is_pdf:
        resolution = max(72, min(400, dpi or PDF_DPI))
        render_started = time.perf_counter()
        try:
            rendered = render_pdf(data, dpi=resolution)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"cannot open PDF: {exc}") from exc
        render_ms = int((time.perf_counter() - render_started) * 1000)
        LOG.info("pdf rendered filename=%s pages=%d dpi=%d ms=%d", filename, len(rendered),
                 resolution, render_ms)
        report({"stage": "rendered", "pages": len(rendered), "ms": render_ms,
                "width": rendered[0][1].width if rendered else 0,
                "height": rendered[0][1].height if rendered else 0})
        try:
            embedded = read_text_layer(data, dpi=resolution) if mode != "ocr" else []
        except Exception as exc:
            LOG.warning("could not read the text layer: %s", exc)
            embedded = []
        carried = sum(1 for page in embedded if page)
        if carried:
            LOG.info("pdf carries its own text on %d of %d pages", carried, len(rendered))
    else:
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except UnidentifiedImageError as exc:
            raise HTTPException(status_code=400, detail="not an image or PDF") from exc
        rendered = [(1, image)]
        embedded = []

    for number, image in rendered:
        page_lines = embedded[number - 1] if number - 1 < len(embedded) else []
        if page_lines and mode != "ocr":
            text_started = time.perf_counter()
            page = {
                "page": number,
                "source": "text",
                "width": image.width,
                "height": image.height,
                "ms": int((time.perf_counter() - text_started) * 1000),
                "timing": {"lines": len(page_lines)},
                "lines": page_lines,
                "text": "\n".join(line["text"] for line in page_lines),
            }
            if is_pdf:
                page["preview"] = preview_data_url(image)
            pages.append(page)
            lines += len(page_lines)
            LOG.info("page %d read from the text layer, %d lines", number, len(page_lines))
            report({"stage": "page_text", "page": number, "text": page["text"],
                    "width": image.width, "height": image.height})
            report({"stage": "page_done", "page": number, "ms": page["ms"],
                    "lines": len(page_lines), "timing": page["timing"]})
            continue

        report({"stage": "page_start", "page": number, "total_pages": len(rendered)})
        result = engine.ocr_image(image, progress=report)
        timing = result.timing
        windows += timing.get("windows", 0)
        skipped += timing.get("windows_skipped", 0)
        read_ms += timing.get("read_ms", 0)
        lines += timing.get("lines", 0)
        timing = dict(result.timing)
        if number == rendered[0][0]:
            timing["render_ms"] = render_ms  # the render cost belongs to the first page
        MONITOR.record_page(timing)
        LOG.info("page done page=%d size=%dx%d ms=%d windows=%d skipped=%d lines=%d",
                 number, result.width, result.height, result.ms, timing.get("windows", 0),
                 timing.get("windows_skipped", 0), timing.get("lines", 0))
        page = {"page": number, "source": "ocr", **result.as_dict()}
        if is_pdf:
            page["preview"] = preview_data_url(image)
        pages.append(page)
        report({"stage": "page_text", "page": number, "text": page["text"],
                "width": result.width, "height": result.height})

    summary = {
        "filename": filename,
        "kind": "pdf" if is_pdf else "image",
        "pages": pages,
        "text": "\n\n".join(page["text"] for page in pages),
        "ms": int((time.perf_counter() - started) * 1000),
        "timing": {
            "render_ms": render_ms,
            "read_ms": read_ms,
            "windows": windows,
            "windows_skipped": skipped,
            "lines": lines,
        },
    }
    MONITOR.record_request({
        "filename": filename,
        "kind": summary["kind"],
        "pages": len(pages),
        "ms": summary["ms"],
        "windows": windows,
        "windows_skipped": skipped,
        "lines": lines,
        "render_ms": render_ms,
        "read_ms": read_ms,
    })
    LOG.info("request done filename=%s kind=%s pages=%d ms=%d windows=%d skipped=%d lines=%d",
             filename, summary["kind"], len(pages), summary["ms"], windows, skipped, lines)
    return summary


@app.post("/api/ocr")
async def ocr(file: UploadFile = File(...), dpi: int = Form(0),
              mode: str = Form("auto")) -> JSONResponse:
    data = await file.read()
    _check_upload(data)
    filename = file.filename or "upload"
    LOG.info("upload filename=%s bytes=%d", filename, len(data))
    return JSONResponse(run_ocr(data, filename, dpi, mode=mode))


@app.post("/api/ocr/stream")
async def ocr_stream(file: UploadFile = File(...), dpi: int = Form(0),
                      mode: str = Form("auto")) -> StreamingResponse:
    data = await file.read()
    _check_upload(data)
    filename = file.filename or "upload"
    LOG.info("upload filename=%s bytes=%d (stream)", filename, len(data))

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    cancelled = threading.Event()

    def publish(kind: str, value) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, (kind, value))
        except RuntimeError:  # the client went away
            pass

    def progress(event: dict) -> None:
        if cancelled.is_set():
            raise Cancelled()
        publish("progress", event)

    def work() -> None:
        try:
            publish("result", run_ocr(data, filename, dpi, progress=progress, mode=mode))
        except Cancelled:
            LOG.info("request cancelled filename=%s", filename)
        except HTTPException as exc:
            publish("error", {"detail": exc.detail})
        except Exception as exc:  # keep the stream alive and say what broke
            LOG.exception("request failed filename=%s", filename)
            publish("error", {"detail": str(exc)})
        finally:
            publish("done", None)

    threading.Thread(target=work, daemon=True, name="ocr-job").start()

    async def stream():
        try:
            while True:
                kind, value = await queue.get()
                if kind == "done":
                    break
                yield f"event: {kind}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n"
        finally:
            cancelled.set()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _check_upload(data: bytes) -> None:
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file larger than 64 MB")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
