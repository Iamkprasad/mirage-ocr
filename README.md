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
- [Using this in a company or a product](#using-this-in-a-company-or-a-product)
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

## Using this in a company or a product

The short version: the code is Apache-2.0, so you may use it commercially and
modify it, and the whole dependency stack is permissive. You do, however,
have to keep the credits, and you inherit the absence of any warranty. This
section spells out what that means in practice.

This is advice on how to read the licences, not legal advice. For a
commercial deployment, have your own lawyer confirm it.

### What you have to do

| What you use | Licence | Your obligations |
| --- | --- | --- |
| this repository's code | Apache-2.0 | Ship a copy of `LICENSE`, keep the `NOTICE`, keep the copyright notices, and say what you changed |
| the Mirage weights (`mirage.cact`) | Apache-2.0 | If you redistribute them, ship the licence and the notice, and say what you changed. The [model card](https://huggingface.co/Cactus-Compute/mirage) is the authority |
| the Cactus runtime | Apache-2.0 | Same, if you redistribute the binaries |
| PDFium and the other libraries | BSD-3, MIT, HPND, MPL-2.0 | Keep the copyright notice in binary distributions. The MPL files (`tqdm`, `certifi`) stay under MPL if you modify them, and you do not modify them |

Apache-2.0 also grants a patent licence from the contributors, and it says the
software is provided **"AS IS", without warranty of any kind**.

### What you must not do

* Do not use **Mirage** or **Cactus** as your product name, or in a way that
  suggests Cactus Compute built, endorses or supports your product. Naming
  the model you are running is fine, attributing your product to its authors
  is not.
* Do not remove the `NOTICE` file from a distribution.
* Do not present the Mirage weights as your own model. They are the work of
  Cactus Compute and the attribution in `NOTICE` is what makes the use
  legitimate.

### Credits you should keep

Keep these in your product's documentation or about page, not just in a
`LICENSE` file nobody reads:

* **Mirage** and the **Cactus runtime** — Cactus Compute, Inc.
  ([cactuscompute.com](https://cactuscompute.com)). Mirage was written by
  Henry Ndubuaku, Karen Mosoyan, Jakub Mroz, Parkirat Sandhu, Justin H. Lee,
  Satyajit Kumar and Roman Shemet.
* **PDFium** — The PDFium Authors, from the Chromium project.
* **FastAPI** — by Sebastián Ramírez, with Starlette and Uvicorn by Tom
  Christie and Encode OSS Ltd, and httpx by the same.
* **python-multipart** — by Andrew Dunham.
* **pypdfium2** — by the pypdfium2 team.
* **Pillow** — by the Python Imaging Fork, originally Secret Labs AB, and the
  Python Software Foundation.
* **Pydantic** — by Samuel Colvin, Eric Jolibois and the pydantic team.
* **AnyIO** — by Alex Grönholm.
* **Click** — by the Pallets team.
* **pytest** — by Holger Krekel and the pytest maintainers.
* **huggingface_hub** — by Hugging Face, Inc.
* **Liberation fonts** — by Red Hat, Inc. and Google, under the SIL Open Font
  Licence, used to draw the test pages.

The full list with links is in [CREDITS.md](CREDITS.md).

### If you need a warranty, an indemnity or support

Nobody is on the hook for this software. Apache-2.0 §7 disclaims all
warranties and liability, and the Mirage weights come with the same
disclaimer. For a production deployment that needs a support contract, a
service level agreement or an indemnity, talk to Cactus Compute directly,
who wrote the model and the runtime and offer commercial terms.

### Before you trust the output

This is OCR: it makes mistakes at the character level, and the model can be
confidently wrong. On the test set the character error rate is 0.000 on clean
pages and 0.021 on a photo of a receipt, but a number like that says nothing
about your documents. Every line comes with a confidence score, and the web
app marks the low scoring ones, so:

* use it for search, indexing and first pass extraction, and keep a human
  in the loop for anything that ends up in a financial, medical, legal or
  compliance record
* measure it on your own documents before you rely on it
* do not treat a high score as a guarantee

### Privacy and the network

The good news for a company: **the documents never leave the machine**. The
model runs locally, so a scanned contract or a payslip never reaches a
third party server.

Two things still talk to the network:

* the first run downloads the engine and the weights from
  `huggingface.co`. In a locked down environment, seed
  `~/.cache/cactus` from a machine that can reach it, or point
  `CACTUS_MIRAGE_WEIGHTS` and `CACTUS_LIB_PATH` at local files and set
  `HF_HUB_OFFLINE=1`
* `cactus-compute` sends anonymous usage counts to Cactus Compute: the
  function name, the package version, the operating system and a random
  install id. Never prompts, never outputs, never images. Set
  `CACTUS_TELEMETRY=0` or `DO_NOT_TRACK=1` if you do not want counts leaving
  your environment

### If you swap PDFium for PyMuPDF

Do not, unless you are ready for the AGPL-3.0 obligations of Artifex's
licence, including its network clause, or you buy a commercial licence from
Artifex. `app/pdf.py` is the only file that touches a PDF, so it is the only
file that has to change.

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
