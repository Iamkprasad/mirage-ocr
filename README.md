# Mirage OCR

**Read text out of images and PDFs, on your own machine, with
[Mirage](https://cactuscompute.com/blog/mirage) — the 22 MB vision model from
Cactus Compute.**

Drop in a photo, a scan or a PDF. Get the text back, with a box and a
confidence score for every line, drawn on top of the page. No account, no API
key, no upload to anyone: the model is downloaded once and then runs locally.

```
POST /api/ocr/stream          ->  server sent events, live progress and text
POST /api/ocr                 ->  one JSON document, for scripts
GET  /api/metrics             ->  the speed monitor
GET  /api/logs                ->  the last few hundred log lines
```

---

## Contents

- [Why this is not a five line wrapper](#why-this-is-not-a-five-line-wrapper)
- [How it works](#how-it-works)
- [Results](#results)
- [Install and run](#install-and-run)
- [The web app](#the-web-app)
- [API](#api)
- [Configuration](#configuration)
- [Project layout](#project-layout)
- [Development](#development)
- [Credits](#credits)
- [Licence](#licence)

---

## Why this is not a five line wrapper

Mirage is a 512 px model. It encodes an image into a **16 x 16 grid of 32 px
cells** and reads the text out of that grid. Hand it a whole A4 page and every
glyph shrinks to a handful of pixels, so the characters start to turn into
each other:

```
a whole A4 page at 300 DPI   ->  'ACNE Industrial suppL1es', 'Invo1ce HA-2291'
```

No amount of extra pixels helps. Raising the DPI does not help either, because
the grid stays 16 x 16.

The fix is to stop sending it whole pages. This app cuts the page into
**overlapping square windows**, reads every window on its own, and merges the
readings back into page coordinates. A window holds a small piece of the page,
so the text inside it is large enough for the grid to resolve:

```
one window of the same page   ->  'ACME Industrial Supplies', 'Invoice #A-2291'
```

Three more things fall out of that approach and are handled here:

* a window edge can cut a long line in half, so cut lines are joined and read
  again on their own, on a square canvas where they are not distorted
* the same line is read by several windows, so readings are merged by box
  overlap and text similarity
* most windows of a page hold no ink at all, and empty windows are skipped,
  which is worth a factor of two

## How it works

```
image or PDF
      |
      v
rasterise (PDF only)      PDFium, 150 / 200 / 300 DPI
      |
      v
cut into overlapping windows          896 px squares, 20 % overlap
      |
      v
skip the empty ones            high pass filter, a window with no ink is free
      |
      v
read each window                    Mirage: text boxes, strings and scores
      |
      v
merge the readings           by box overlap and text similarity, boxes unioned
      |
      v
re-read the cut lines        one line at a time, on a square canvas
      |
      v
sort into reading order      rows by vertical overlap, then left to right
      |
      v
JSON with text, boxes and scores
```

Every step reports to the log, the monitor and the browser as it happens.

## Results

Measured on 8 ARM cores with `cactus-compute` 4.2.0 and the released 2 bit
Mirage weights. Character error rate is computed against the ground truth in
`tests/samples.py`.

| Sample | Character error rate | Time |
| --- | --- | --- |
| Synthetic invoice page, 200 DPI | 0.006 | 2.0 s |
| The same page as a PDF | 0.000 | 2.4 s per page |
| Page photographed with a phone (perspective, uneven light, blur, JPEG) | 0.000 | 2.3 s |
| Receipt photo (small, skewed) | 0.021 | 0.5 s |

The only mistakes are one character on the long IBAN line of the synthetic
page, and the masked card number on the receipt, `****4417`, which comes out as
`w4417`.

On the stop sign from the
[Mirage announcement](https://cactuscompute.com/blog/mirage) the app reads
`ARRÉT`, which is what the model itself reports, accent included.

## Install and run

Python 3.10 or newer.

```sh
git clone <your fork>
cd mirage-ocr
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --port 8000
```

Or just:

```sh
./run.sh
```

Then open <http://127.0.0.1:8000>.

The first request downloads the Cactus engine and the Mirage weights (about
25 MB) from Hugging Face and takes a little longer than the rest. Both are
cached under `~/.cache/cactus`, so this only happens once.

On a machine that is offline, copy the cache across and point the app at it:

```sh
export CACTUS_MIRAGE_WEIGHTS=/path/to/mirage.cact
export CACTUS_LIB_PATH=/path/to/libcactus.so
export HF_HUB_OFFLINE=1
```

## The web app

The browser talks to `POST /api/ocr/stream`, which answers with server sent
events, so the page never looks stuck:

* a progress bar and a line that says what is happening right now
  (`page 1 of 2 · window 4 of 12 · 550ms`)
* the text and the boxes appear window by window, while the page is still
  being read
* a log panel with every step, the same lines that go to `logs/app.log`
* a strip of speed numbers, refreshed every three seconds

Boxes are drawn over the page. Low scoring lines are drawn in orange.

```
+--------------------------------------------------+
|  page 1 of 2 · window 4 of 12 · 550ms             |
|  [progress bar]                                   |
+--------------------------------------------------+
| ACME Industrial Supplies   |  ACME Industrial    |
| Invoice #A-2291            |  Supplies            |
+--------------------------------------------------+
```

## API

### `POST /api/ocr`

```sh
curl -F "file=@document.pdf" -F "dpi=200" http://127.0.0.1:8000/api/ocr
```

```json
{
  "filename": "document.pdf",
  "kind": "pdf",
  "pages": [
    {
      "page": 1,
      "width": 1653,
      "height": 2339,
      "ms": 3276,
      "timing": {"windows": 12, "windows_skipped": 8, "read_ms": 2270, "refine_ms": 812},
      "lines": [
        {"text": "Total amount: $1,480.00", "bbox": [152, 523, 563, 572], "score": 0.769}
      ],
      "text": "ACME Industrial Supplies\nInvoice #A-2291\n..."
    }
  ],
  "text": "ACME Industrial Supplies\nInvoice #A-2291\n...",
  "ms": 6315
}
```

`bbox` is `[x1, y1, x2, y2]` in pixels of the page image, so a client can
draw the boxes straight on top of the page.

### `POST /api/ocr/stream`

The same job, answered with server sent events:

| Event | When |
| --- | --- |
| `model` | the Mirage engine is loaded |
| `rendered` | the PDF pages are rasterised |
| `page_start` | a page begins |
| `window` | a window was read or skipped, with the lines found so far |
| `page_done` | a page is finished, with its timings |
| `page_text` | the finished text of a page |
| `result` | the same document `POST /api/ocr` returns |
| `error` | something went wrong |

```sh
curl -N -F "file=@document.pdf" -F "dpi=200" http://127.0.0.1:8000/api/ocr/stream
```

### `GET /api/metrics`

The speed monitor. Running totals and the last requests.

```json
{
  "requests": 12, "pages": 20, "windows": 240, "windows_skipped": 96, "lines": 140,
  "avg_ms_per_page": 1909, "avg_ms_per_window": 145,
  "last": {"filename": "document.pdf", "ms": 6315, "windows": 24, "windows_skipped": 16},
  "history": [{"filename": "document.pdf", "ms": 6315, "pages": 2}]
}
```

### `GET /api/logs`

The last few hundred log records, the same ones the log panel shows.

### `GET /api/health`

```json
{"ok": true, "tile_size": 896, "pdf_dpi": 200}
```

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `OCR_TILE_SIZE` | `896` | Window size in pixels. Smaller is more accurate, larger is faster |
| `OCR_OVERLAP` | `0.2` | How much neighbouring windows overlap |
| `OCR_TEXT_CONF` | `0.2` | Minimum score for a text detection |
| `OCR_REFINE` | `1` | Set to `0` to stop re-reading lines that a window edge cut |
| `OCR_PDF_DPI` | `200` | Resolution used to rasterise PDFs, the UI can override it per upload |
| `OCR_LOG_FILE` | `logs/app.log` | Where the log file goes |
| `CACTUS_MIRAGE_WEIGHTS` | | Path to a local `mirage.cact` |
| `CACTUS_LIB_PATH` | | Path to a local `libcactus.so` |
| `HF_HUB_OFFLINE` | | Set to `1` to forbid downloads |

### Where the time goes

The monitor showed that reading the windows dominates, and that most windows
of a page hold no ink at all. Two changes came out of it, measured on an A4
scan:

| Change | Model calls | Time |
| --- | --- | --- |
| Read every window | 12 | 3.7 s |
| Skip windows with no ink | 2 | 2.3 s |
| Do not re-read cut lines (`OCR_REFINE=0`) | 2 | saves a further 0.8 s per page, long lines may break |

A window counts as empty when it differs from a blurred copy of itself by
almost nothing, so smooth shading is empty and a line of text is not. The
character error rate does not change when empty windows are skipped.

What is left is the model itself, about 450 ms per window on this machine.
The Cactus runtime serialises every call, so the only way to go faster is to
read windows in separate processes, which the API shape already allows.

## Project layout

```
app/engine.py       tiling, merging, refinement around one Mirage instance
app/pdf.py          PDF pages to images
app/main.py         FastAPI routes and the streaming job runner
app/log.py          logging to the console, a file and a ring buffer for the UI
app/metrics.py      the speed monitor
app/static/         the web app: plain HTML, CSS and JavaScript, no build step
tests/samples.py    ground truth samples, also writes files under tests/samples/
tests/test_ocr.py   end to end checks
tests/quality.py    character error rate report
tests/check_stream.js     the contract between the server and the browser
tests/check_frontend.js   the browser code, run against a recorded stream
```

## Development

```sh
.venv/bin/python -m pytest              # end to end checks
.venv/bin/python tests/quality.py       # character error rate per sample
.venv/bin/python -m tests.samples       # write the sample files to disk
```

The samples land in `tests/samples/`, so the app can be pointed at them
straight away:

```sh
curl -F "file=@tests/samples/document.pdf" -F "dpi=200" http://127.0.0.1:8000/api/ocr
curl -F "file=@tests/samples/document_photo.jpg" http://127.0.0.1:8000/api/ocr
```

The pytest run also plays the server sent events through
`tests/check_stream.js` and the browser code through `tests/check_frontend.js`,
so a change to the event shape or to the page cannot pass unnoticed.

## Credits

This project is built on [Mirage](https://cactuscompute.com/blog/mirage) and
the [Cactus runtime](https://github.com/cactus-compute/cactus) by
[Cactus Compute](https://cactuscompute.com), both Apache-2.0. The weights are
downloaded from [Hugging Face](https://huggingface.co/Cactus-Compute/mirage),
pinned to revision `76c1e9e5`.

The full list of libraries, fonts and sample images, with their licences, is
in [CREDITS.md](CREDITS.md). Every dependency is permissive: Apache-2.0,
BSD-3-Clause, MIT, the Unicode licence and the PIL licence. Nothing here is
copyleft, so there is nothing for a deployer to publish beyond this
repository.

Two things worth knowing:

* **PDF rendering uses PDFium** (BSD-3-Clause) rather than PyMuPDF, which is
  AGPL-3.0 and has a network clause.
* **`cactus-compute` sends anonymous usage counts** to Cactus Compute: the
  function name, the package version, the operating system and a random
  install id. Never prompts, never outputs, never images. Turn it off with
  `CACTUS_TELEMETRY=0` or `DO_NOT_TRACK=1`.

This repository is an independent third party project. It is not affiliated
with, endorsed by, or sponsored by Cactus Compute, Inc.

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

Mirage and the Cactus runtime are the work of Cactus Compute, Inc. and are
used here under the Apache License, Version 2.0.
