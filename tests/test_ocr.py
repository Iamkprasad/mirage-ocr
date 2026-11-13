"""End-to-end OCR checks against ground truth."""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil

import pytest
from fastapi.testclient import TestClient

from app.engine import OcrEngine
from app.main import app
from tests.samples import (DOCUMENT_LINES, RECEIPT_LINES, make_document_image, make_document_photo,
                           make_pdf, make_receipt_image)


def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def character_error_rate(ground_truth: str, hypothesis: str) -> float:
    reference, candidate = normalise(ground_truth), normalise(hypothesis)
    if not reference:
        return 0.0
    matcher = difflib.SequenceMatcher(None, reference, candidate)
    errors = sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    )
    return errors / len(reference)


def best_error_rate(lines, ground_truth: str) -> float:
    return min((character_error_rate(ground_truth, line.text) for line in lines), default=1.0)


@pytest.fixture(scope="module")
def engine() -> OcrEngine:
    return OcrEngine(tile_size=896, overlap=0.2)


def test_document_image_is_read_correctly(engine: OcrEngine) -> None:
    result = engine.ocr_image(make_document_image())
    for line in DOCUMENT_LINES:
        assert best_error_rate(result.lines, line) <= 0.05, f"missing or wrong: {line!r} in {result.lines}"


def test_boxes_point_at_the_text(engine: OcrEngine) -> None:
    result = engine.ocr_image(make_document_image())
    for line in result.lines:
        x0, y0, x1, y1 = line.bbox
        assert 0 <= x0 < x1 <= result.width
        assert 0 <= y0 < y1 <= result.height


def test_pdf_pages_are_read(engine: OcrEngine) -> None:
    from app.pdf import render_pdf

    pages = render_pdf(make_pdf(pages=2), dpi=200)
    assert len(pages) == 2
    for number, image in pages:
        result = engine.ocr_image(image)
        for line in DOCUMENT_LINES:
            assert best_error_rate(result.lines, line) <= 0.08, f"page {number}: {line!r}"


def test_receipt_photo_is_read(engine: OcrEngine) -> None:
    result = engine.ocr_image(make_receipt_image())
    assert len(result.lines) >= 4
    assert min(best_error_rate(result.lines, line) for line in RECEIPT_LINES) <= 0.15


def test_document_photo_is_read(engine: OcrEngine) -> None:
    result = engine.ocr_image(make_document_photo())
    for line in DOCUMENT_LINES:
        assert best_error_rate(result.lines, line) <= 0.05, f"missing or wrong: {line!r}"


def test_api_returns_text_and_boxes() -> None:
    import io

    buffer = io.BytesIO()
    make_document_image().save(buffer, "PNG")
    client = TestClient(app)
    response = client.post(
        "/api/ocr",
        files={"file": ("document.png", buffer.getvalue(), "image/png")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["kind"] == "image"
    assert payload["pages"][0]["lines"]
    assert "Invoice" in payload["text"]
    assert payload["pages"][0]["width"] > 0


def test_stream_reports_progress_and_finishes() -> None:
    import io

    buffer = io.BytesIO()
    make_document_image().save(buffer, "PNG")
    client = TestClient(app)
    with client.stream(
        "POST",
        "/api/ocr/stream",
        files={"file": ("document.png", buffer.getvalue(), "image/png")},
    ) as response:
        assert response.status_code == 200
        frames = []
        for chunk in response.iter_lines():
            if chunk.startswith("data:"):
                frames.append(json.loads(chunk[5:]))
    stages = [frame.get("stage") for frame in frames if isinstance(frame, dict) and "stage" in frame]
    assert "model" in stages
    assert "window" in stages
    assert any("timing" in frame for frame in frames if isinstance(frame, dict))
    result = [frame for frame in frames if isinstance(frame, dict) and frame.get("kind")]
    assert result and "Invoice" in result[0]["text"]


def test_monitor_and_logs_are_exposed() -> None:
    client = TestClient(app)
    metrics = client.get("/api/metrics").json()
    assert metrics["pages"] >= 0
    assert "avg_ms_per_window" in metrics
    logs = client.get("/api/logs").json()
    assert isinstance(logs["entries"], list)


def test_a_pdf_with_its_own_text_is_read_from_it() -> None:
    """A PDF written by a word processor carries the text it was written with."""
    import io

    import pypdfium2 as pdfium

    from app.pdf import read_text_layer

    data = make_pdf(pages=1)
    pages = read_text_layer(data, dpi=200)
    assert len(pages) == 1
    assert pages[0], "the text layer should not be empty"
    text = " ".join(line["text"] for line in pages[0])
    for line in DOCUMENT_LINES:
        assert line in text, f"missing from the text layer: {line!r}"
    x0, y0, x1, y1 = pages[0][0]["bbox"]
    assert 0 <= x0 < x1 and 0 <= y0 < y1

    buffer = io.BytesIO()
    make_document_image().save(buffer, "PNG")
    client = TestClient(app)
    response = client.post(
        "/api/ocr",
        files={"file": ("document.pdf", data, "application/pdf")},
        data={"mode": "auto"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["pages"][0]["source"] == "text"
    for line in DOCUMENT_LINES:
        assert line in payload["text"]


def test_the_model_is_used_when_a_pdf_has_no_text() -> None:
    """A scanned page is a picture, so the model has to read it."""
    import io

    buffer = io.BytesIO()
    make_document_image().save(buffer, "PNG")
    client = TestClient(app)
    response = client.post(
        "/api/ocr",
        files={"file": ("document.png", buffer.getvalue(), "image/png")},
        data={"mode": "auto"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["pages"][0]["source"] == "ocr"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_stream_and_frontend_contracts(tmp_path) -> None:
    """The browser code reads specific fields, so check the stream still has them."""
    import io
    import subprocess

    buffer = io.BytesIO()
    make_document_image().save(buffer, "PNG")
    client = TestClient(app)
    with client.stream(
        "POST",
        "/api/ocr/stream",
        files={"file": ("document.png", buffer.getvalue(), "image/png")},
    ) as response:
        payload = "".join(response.iter_text())
    stream_file = tmp_path / "stream.txt"
    stream_file.write_text(payload)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for script in ("check_stream.js", "check_frontend.js"):
        result = subprocess.run(
            ["node", os.path.join(root, "tests", script), str(stream_file)],
            capture_output=True, text=True, cwd=root,
        )
        assert result.returncode == 0, f"{script}: {result.stdout}{result.stderr}"
