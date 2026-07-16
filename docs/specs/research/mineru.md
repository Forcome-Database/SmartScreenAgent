# MinerU Integration — Research Notes

> **Superseded on 2026-07-16:** This document originally evaluated a self-hosted
> MinerU 3.x service. Production now uses the
> [official MinerU API v4](https://mineru.net/doc/docs/index_en/). The active flow is:
> request signed upload URLs with `POST /api/v4/file-urls/batch`; upload raw file bytes
> to each returned URL without the API bearer token; poll
> `GET /api/v4/extract-results/batch/{batch_id}`; then validate and download
> `full_zip_url`. API, upload, and result traffic use isolated clients with exact-host
> allowlists, redirects disabled, bounded downloads, and no signed URLs in logs.
> Everything below this notice is retained as historical research and is not the
> implemented WP2 contract.

**Task:** P2 Task 0 — verify MinerU (PDF/Office parsing library) so that Task 4 (MinerU client) can be implemented without guesswork.

**Researched on:** 2026-05-13

## TL;DR

- Repo exists at **<https://github.com/opendatalab/MinerU>**, actively developed by OpenDataLab.
- Latest stable release: **`v3.1.11`** (May 9, 2026), part of the 3.1.x line where the architecture shifted: the CLI is now an orchestration client of an internal `mineru-api` FastAPI service. Verified via the [README on GitHub](https://github.com/opendatalab/MinerU).
- Pip package is **`mineru`** (the old `magic-pdf` package — verified live on [PyPI magic-pdf](https://pypi.org/project/magic-pdf/) at v1.3.12, last released 2025-05-24 — is the 2.x line and is **not** what we want; 3.x ships as the new `mineru` package). Install with `uv pip install -U "mineru[all]"` per the README.
- Supports **PDF, image, DOCX, PPTX, XLSX** inputs natively — no python-docx fallback needed for `.docx`.
- Both **self-hosted HTTP FastAPI server** and **Python-client orchestration** are supported. They are not two unrelated APIs: in 3.x the Python library *is* an HTTP client of `mineru-api`. When you do not supply an `api_url`, the library spins up a *local temporary* mineru-api process and talks to it. This shapes the recommendation below.
- **Recommendation:** run `mineru-api` as a **separate self-hosted HTTP container** (its own service in `docker-compose`, GPU-attached) and call it from the Celery worker with a thin `httpx` client. Do **not** install `mineru[all]` into the FastAPI container — it pulls vLLM, PyTorch, and gigabytes of model weights that we do not want in a request-serving image. See [§5 Recommendation](#5-recommendation-and-deployment-shape).

---

## 1. Repository, Version, Install

| Field | Value | Source |
|---|---|---|
| Repository URL | `https://github.com/opendatalab/MinerU` | [GitHub](https://github.com/opendatalab/MinerU) |
| Latest stable version | **3.1.11** (May 9, 2026); 3.1.0 was the major 3.1 release on 2026-04-18 | [PyPI mineru](https://pypi.org/project/mineru/), [GitHub README](https://github.com/opendatalab/MinerU) |
| Pip package name | **`mineru`** (the 2.x line was published as `magic-pdf`; 3.x is the renamed `mineru` package) | [PyPI mineru](https://pypi.org/project/mineru/), [PyPI magic-pdf](https://pypi.org/project/magic-pdf/) |
| Recommended install | `uv pip install -U "mineru[all]"` | [README — Installation](https://github.com/opendatalab/MinerU) |
| Verification date | **2026-05-13** | this document |

Install command verbatim from the README:

```bash
pip install --upgrade pip
pip install uv
uv pip install -U "mineru[all]"
```

Source: <https://github.com/opendatalab/MinerU> (README "Installation").

The `[all]` extra brings in the heavy stack (vLLM, PyTorch, model loaders). A lighter install without `[all]` exists for users who only need the `pipeline` backend, but it is **not** documented well enough in the README to commit to in code — `TBD-verify-with-runtime: confirm whether plain "pip install mineru" gives a usable pipeline-only backend without the vLLM extras`.

---

## 2. Supported Input Formats

Verified via the [PyPI page](https://pypi.org/project/mineru/) and the [README](https://github.com/opendatalab/MinerU):

> "A practical document parsing tool for converting PDF, images, DOCX, PPTX, and XLSX into Markdown and JSON"

Additionally confirmed in the demo source — [`demo/demo.py`](https://github.com/opendatalab/MinerU/blob/master/demo/demo.py) imports `pdf_suffixes`, `image_suffixes`, **`office_suffixes`** from `mineru.cli.common` and unions them as the accepted input set:

```python
from mineru.cli.common import image_suffixes, office_suffixes, pdf_suffixes
SUPPORTED_INPUT_SUFFIXES = set(pdf_suffixes + image_suffixes + office_suffixes)
```

Source (verbatim, lines 9 and 12 of `demo/demo.py`): <https://github.com/opendatalab/MinerU/blob/master/demo/demo.py>.

### DOCX decision

**DOCX is a first-class supported input format.** We do **not** need a python-docx fallback for the happy path. The MinerU API accepts `.docx` directly and returns Markdown + JSON the same way it does for PDF.

If for operational reasons (e.g., MinerU service unreachable) we still need a degraded fallback, `python-docx` + `mammoth` (for `.docx → markdown`) is the standard workaround, but this is **out of scope** for Task 4 and should be a separate ticket if added at all. — `TBD-verify-with-runtime: confirm DOCX → Markdown output quality is comparable to PDF parsing on representative customer files before relying on it; if it is poor, escalate.`

---

## 3. Calling Modes

MinerU 3.1.x offers **three** invocation surfaces. All three are documented in official sources:

### (a) CLI

```bash
mineru -p <input_path> -o <output_path>
mineru -p <input_path> -o <output_path> -b pipeline
```

Source: [README — Quickstart](https://github.com/opendatalab/MinerU) (verbatim).

Not useful for us — we are not invoking subprocesses from FastAPI/Celery.

### (b) Self-hosted HTTP FastAPI server (`mineru-api`)

Started via the entry-point script `mineru-api` (installed by the pip package), or via the official Docker image. The default port is **8000** and Swagger UI is mounted at `/docs`.

Endpoints exposed (from README and `mineru/cli/fast_api.py`):

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/tasks` | Async: submit a parse task, returns `task_id` |
| `GET`  | `/tasks/{task_id}` (status) and result download routes | Poll status, fetch result ZIP |
| `POST` | `/file_parse` | Synchronous parse (legacy plugin compatibility) |
| `GET`  | `/health` | Health check (used by docker-compose healthcheck) |
| `GET`  | `/docs` | Swagger UI |

Source: README "API Server" section at <https://github.com/opendatalab/MinerU>; healthcheck path `http://localhost:8000/health` confirmed in [`docker/compose.yaml`](https://github.com/opendatalab/MinerU/blob/master/docker/compose.yaml). The exact route decorators in `mineru/cli/fast_api.py` were not fully readable in WebFetch — `TBD-verify-with-runtime: bring up the container and curl /docs/openapi.json to capture the canonical request/response schemas and the precise status/result paths under /tasks/{task_id}`.

Docker compose snippet for the `mineru-api` service, verbatim from [`docker/compose.yaml`](https://github.com/opendatalab/MinerU/blob/master/docker/compose.yaml):

```yaml
  mineru-api:
    image: mineru:latest
    container_name: mineru-api
    restart: always
    profiles: ["api"]
    ports:
      - 8000:8000
    environment:
      MINERU_MODEL_SOURCE: local
    entrypoint: mineru-api
    command:
      --host 0.0.0.0
      --port 8000
    ulimits:
      memlock: -1
      stack: 67108864
    ipc: host
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              device_ids: ["0"]
              capabilities: [gpu]
```

Bring-up command, verbatim from the [docker_deployment.md docs](https://github.com/opendatalab/MinerU/blob/master/docs/en/quick_start/docker_deployment.md):

```bash
docker compose -f compose.yaml --profile api up -d
```

The build instruction is also documented:

```bash
wget https://gcore.jsdelivr.net/gh/opendatalab/MinerU@master/docker/global/Dockerfile
docker build -t mineru:latest -f Dockerfile .
```

(Source: same docker_deployment.md.) The Dockerfile is at `docker/global/Dockerfile` and uses `vllm/vllm-openai` as its base.

### (c) Python library (orchestration client of `mineru-api`)

Important architectural fact: in 3.x, the official `demo/demo.py` shows that the Python library is itself a thin HTTP client around `mineru-api`. The "library mode" is *not* an in-process parsing call — it either:

1. connects to a remote `mineru-api` via `api_url`, or
2. starts a **local temporary** `mineru-api` subprocess (`LocalAPIServer().start()`) in the same machine and talks to it over loopback.

Verbatim excerpt from [`demo/demo.py`](https://github.com/opendatalab/MinerU/blob/master/demo/demo.py) (the actual invocation; see source for the full file):

```python
from mineru.cli import api_client as _api_client
from mineru.cli.common import image_suffixes, office_suffixes, pdf_suffixes
from mineru.utils.guess_suffix_or_lang import guess_suffix_by_path

# ... build the form data and the upload assets ...

async with httpx.AsyncClient(
    timeout=_api_client.build_http_timeout(),
    follow_redirects=True,
) as http_client:
    if api_url is None:
        local_server = _api_client.LocalAPIServer()
        base_url = local_server.start()
        server_health = await _api_client.wait_for_local_api_ready(
            http_client, local_server,
        )
    else:
        server_health = await _api_client.fetch_server_health(
            http_client, _api_client.normalize_base_url(api_url),
        )

    submit_response = await _api_client.submit_parse_task(
        base_url=server_health.base_url,
        upload_assets=upload_assets,
        form_data=form_data,
    )
    await _api_client.wait_for_task_result(
        client=http_client,
        submit_response=submit_response,
        task_label=task_label,
        status_snapshot_callback=on_status_update,
    )
    result_zip_path = await _api_client.download_result_zip(
        client=http_client,
        submit_response=submit_response,
        task_label=task_label,
    )
```

Source URL: <https://github.com/opendatalab/MinerU/blob/master/demo/demo.py> (verbatim subset).

The form-data builder is also part of the documented surface:

```python
_api_client.build_parse_request_form_data(
    lang_list=[language],
    backend=backend,          # "hybrid-auto-engine" | "pipeline" | "vlm-auto-engine" | "vlm-http-client" | "hybrid-http-client"
    parse_method=parse_method, # "auto" | "txt" | "ocr"
    formula_enable=formula_enable,
    table_enable=table_enable,
    server_url=server_url,
    start_page_id=start_page_id,
    end_page_id=end_page_id,
    return_md=True,
    return_middle_json=False,
    return_model_output=False,
    return_content_list=False,
    return_images=True,
    response_format_zip=True,
    return_original_file=False,
)
```

Verbatim from `demo/demo.py`, source: <https://github.com/opendatalab/MinerU/blob/master/demo/demo.py>.

### Backends (the `-b` / `backend` parameter)

Documented in `demo/demo.py` comments verbatim:

> ```
> # "hybrid-auto-engine"   -> local hybrid parsing, recommended default
> # "pipeline"             -> more general OCR/text pipeline
> # "vlm-auto-engine"      -> local VLM parsing
> # "vlm-http-client"      -> remote OpenAI-compatible VLM server
> # "hybrid-http-client"   -> remote OpenAI-compatible hybrid server
> ```

The `pipeline` backend is the CPU-friendly one. The others require a CUDA GPU (Volta or later, 8GB+ VRAM, CUDA driver ≥ 12.9.1 per the docker_deployment.md docs).

---

## 4. Output Schema

Per the README, MinerU emits **Markdown + JSON** plus extracted images, all packed into a result ZIP when `response_format_zip=True`. The async `/tasks` flow expects the client to download a ZIP and extract it (`_api_client.safe_extract_zip` in the demo).

The README mentions "multimodal and NLP Markdown, JSON sorted by reading order".

`TBD-verify-with-runtime: capture an example result ZIP layout for a real PDF — what files are inside (likely `*.md`, `*_content_list.json`, `*_middle.json`, `images/`), and what fields are in each JSON?` — this matters for the scoring engine because Task 4 must extract structured text + table content for scoring, not just the rendered Markdown.

---

## 5. Recommendation and Deployment Shape

**Use option (b): run `mineru-api` as its own self-hosted Docker service, called from our Celery worker over HTTP with a thin `httpx`-based client.**

### Why not embed the library in our FastAPI container

- `mineru[all]` pulls **vLLM, PyTorch (CUDA), and several GB of model weights** on first run. Bloats the API image, slows cold starts, makes the request-serving container GPU-coupled.
- The "library mode" (option c) is **not really in-process** anyway — under the hood it boots a `mineru-api` subprocess. There is no architectural advantage to embedding it; we just lose isolation and gain dependency surface.

### Why not embed in the Celery worker container

- Same bloat argument. Celery worker images stay small if parsing is a remote call.
- A model crash in `mineru-api` (vLLM OOM, model load failure) would crash the Celery worker. With a separate service, Celery just retries the HTTP call.
- Scaling parsing independently from job orchestration is easier when parsing is its own service.

### Proposed shape

```
┌────────────┐  HTTP   ┌────────────┐  HTTP   ┌─────────────┐
│  FastAPI   │ ──────► │  Celery    │ ──────► │  mineru-api │
│ (API)      │  Redis  │  worker    │   :8000 │  (GPU)      │
└────────────┘  queue  └────────────┘         └─────────────┘
```

- The Celery worker container has `httpx` only.
- A new `mineru-api` service is added to `docker-compose` (separate profile, e.g. `--profile parsing`) using the official `mineru:latest` image once we have built it from `docker/global/Dockerfile`.
- Env var `MINERU_API_URL` (e.g. `http://mineru-api:8000`) is read by the Task 4 client.
- For local dev without a GPU, an integration test can fall back to `pipeline` backend on CPU — `TBD-verify-with-runtime: confirm "pipeline" backend actually runs without CUDA in the docker image; the compose file always reserves an nvidia GPU.`

### Open questions for runtime verification

These must be resolved during Task 4 implementation or with a quick smoke test against a running container — they are **not** in the README:

1. `TBD-verify-with-runtime: exact request/response schema of POST /tasks` — request is multipart (we see `submit_parse_task` uses `UploadAsset` + `form_data`), but the field names and JSON status payload need to be captured from `/openapi.json`.
2. `TBD-verify-with-runtime: the status polling URL — likely GET /tasks/{task_id}, but confirm whether the result-ZIP download is a separate path or returned inline.`
3. `TBD-verify-with-runtime: max upload size and request timeout defaults — _api_client.build_http_timeout() suggests there are sensible defaults but the value is not in docs.`
4. `TBD-verify-with-runtime: rate limits — none are documented for the self-hosted server; concurrency appears bounded by the in-process AsyncTaskManager queue (queued_ahead is surfaced in TaskStatusSnapshot). Capture the worker concurrency setting.`
5. `TBD-verify-with-runtime: model auto-download behavior on first request — the demo references env var MINERU_MODEL_SOURCE=modelscope as a workaround for users behind the GFW; we should pre-bake or pre-warm models in the image to avoid first-request latency.` The Chinese-language comment in demo.py is verbatim: `如果您由于网络问题无法下载模型，可以设置环境变量MINERU_MODEL_SOURCE为modelscope使用免代理仓库下载模型`.

---

## 6. Minimal Python Snippet We Will Build On (verbatim source)

The shortest officially-documented invocation lives in [`demo/demo.py`](https://github.com/opendatalab/MinerU/blob/master/demo/demo.py). The core call sequence we will mirror in our Task 4 client (copied verbatim from that file):

```python
# Copyright (c) Opendatalab. All rights reserved.
import asyncio
from pathlib import Path
import httpx
from mineru.cli import api_client as _api_client

async def parse_one(input_file: Path, api_url: str, output_dir: Path) -> Path:
    form_data = _api_client.build_parse_request_form_data(
        lang_list=["ch"],
        backend="hybrid-auto-engine",
        parse_method="auto",
        formula_enable=True,
        table_enable=True,
        server_url=None,
        start_page_id=0,
        end_page_id=None,
        return_md=True,
        return_middle_json=False,
        return_model_output=False,
        return_content_list=False,
        return_images=True,
        response_format_zip=True,
        return_original_file=False,
    )
    upload_assets = [_api_client.UploadAsset(path=input_file, upload_name=input_file.name)]

    async with httpx.AsyncClient(
        timeout=_api_client.build_http_timeout(),
        follow_redirects=True,
    ) as http_client:
        server_health = await _api_client.fetch_server_health(
            http_client, _api_client.normalize_base_url(api_url),
        )
        submit_response = await _api_client.submit_parse_task(
            base_url=server_health.base_url,
            upload_assets=upload_assets,
            form_data=form_data,
        )
        await _api_client.wait_for_task_result(
            client=http_client,
            submit_response=submit_response,
            task_label=input_file.name,
        )
        result_zip = await _api_client.download_result_zip(
            client=http_client,
            submit_response=submit_response,
            task_label=input_file.name,
        )
    _api_client.safe_extract_zip(result_zip, output_dir)
    result_zip.unlink(missing_ok=True)
    return output_dir
```

Source: <https://github.com/opendatalab/MinerU/blob/master/demo/demo.py> (the snippet above is a verbatim subset; full file is ~190 lines).

**Note for Task 4:** Using `mineru.cli.api_client` creates a Python dependency on `mineru` in the calling container. If we want a *pure* HTTP client with **zero** `mineru` PyPI dependency in the Celery worker, we should write our own `httpx` calls against the documented endpoints (`POST /tasks`, `GET /tasks/{id}`, ZIP download). That is the preferred path — `TBD-verify-with-runtime: capture the raw HTTP traffic from demo.py once and re-implement it in our own client.`

---

## 7. Hardware and Model Requirements

From [docker_deployment.md](https://github.com/opendatalab/MinerU/blob/master/docs/en/quick_start/docker_deployment.md):

- GPU: Volta architecture or later, ≥ **8 GB** VRAM.
- CUDA driver: **≥ 12.9.1** (`nvidia-smi`).
- Shared memory: **`--shm-size 32g`** (or `ipc: host` in compose).
- `--gpus all` required.
- Docker on macOS is **not** supported (no MPS/MLX bridging); Linux or Windows WSL2 only.
- `MINERU_MODEL_SOURCE=local` (compose default) avoids re-downloading at start; `modelscope` is the alternative for mainland-China networks.

`TBD-verify-with-runtime: model weight size on disk after first download — the README doesn't quote a number but vLLM-based VLM weights are typically multi-GB; budget at least 10 GB of volume space.`

---

## 8. Summary for Task 4

| Decision | Choice | Reason |
|---|---|---|
| Calling mode | **HTTP, self-hosted `mineru-api` service** | Isolation; keeps API/worker images thin; matches the official 3.x architecture (the library is already a client) |
| Where called from | **Celery worker**, not FastAPI | Parsing is a long-running job |
| Client library | **Our own `httpx` client** (no `mineru` pip dep in worker) | Minimal surface; `mineru.cli.api_client` requires importing `mineru` which transitively pulls heavy deps |
| Input formats | PDF, DOCX, PPTX, XLSX, images — all native | Native MinerU support; no python-docx needed |
| Backend (compose service env) | `hybrid-auto-engine` default; `pipeline` for CPU fallback | Documented in demo.py comments |
| Container image | `mineru:latest` built from `docker/global/Dockerfile`, port 8000 | Documented in compose.yaml |
| Env var the client reads | `MINERU_API_URL` (our naming) → `http://mineru-api:8000` | New, our choice |
| Pre-Task-4 verification | Run `mineru-api` once, capture `/openapi.json`, smoke-test `POST /tasks` with a sample PDF and a sample DOCX, save the result-ZIP contents to confirm output schema | See `TBD-verify-with-runtime` items above |

All `TBD-verify-with-runtime` markers above must be resolved by spinning up the container during Task 4. None block writing the client interface; they only block hard-coding wire-level details.
