# BuildERP — Python

A Python application for construction specifications, evidence-linked submittal review, drawing schedule review, equipment logs, and document chat.

The application runs as one **Flask** service. **Jinja** renders the interface; **PyMuPDF** handles PDF parsing, previews, and crops; **Turso** stores cloud accounts and project state; local development can use **SQLite**. Gemini calls, encryption, validation, and exports run in Python. No Node.js, Next.js, or OpenCV sidecar is required.

**OpenCV table detection runs inside the Python service.** It locates ruled table regions on drawing PDFs, presents suggestions for review, and sends only accepted or manually saved crops to schedule extraction. Detection itself needs no Gemini key.

## Deploy on Vercel

Start with [DEPLOY-VERCEL.md](DEPLOY-VERCEL.md). This release adds Turso, direct R2 uploads, a Flask entrypoint, CDN assets, and deployment smoke checks. It is prepared for preview deployment; real Vercel/OpenCV/Gemini validation is still required before production cutover.

## Run locally

Use Python 3.12.

```bash
python -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows PowerShell instead:
# .venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` (`cp .env.example .env` on macOS/Linux or `Copy-Item .env.example .env` in PowerShell). Generate a settings encryption secret:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the value into `SETTINGS_SECRET` in `.env`, then start:

```bash
python run.py
```

Open **http://127.0.0.1:8000**. Create an account; the first account becomes the owner. Local storage is created in `data/`. Keep that directory across restarts. In Settings, the owner can save a Gemini key. Accounts, projects, PDF text parsing, page previews, table detection, and existing reviews work without an AI key; OCR, extraction, embeddings, and chat require one.

## Workflow

1. Create a project and upload specification or drawing PDFs (10 MB / 200 pages each).
2. Open a specification and **Prepare document**. Native PDF text is read locally; scanned pages use Gemini OCR.
3. **Generate submittal log**, then inspect requirements and catalog products against the source. Processing saves batches and resumes on retry.
4. Review each requirement. Approval requires a source confirmation, valid quoted evidence, and notes for warnings. Concurrent edits are rejected rather than overwritten.
5. Attach catalog products to the obligation, combine them, or create individual product drafts.
6. **Prepare chat** to index a PDF, then ask document-scoped questions with source links.
7. For drawings, select **Table & crop review** → **Detect tables**. Inspect suggestions, select the schedules you need, and click **Accept selected tables**. Edit crop borders or add manual regions, then **Extract schedule rows**. Build the equipment log from the project page and confirm CSI selections.
8. Export draft or approved registers as Excel, JSON, or Markdown. An approved full register requires completed specification review, including empty-result confirmation.

## Table detection

Install the updated `requirements.txt` in the same Python environment as the app, then restart. It pins `opencv-python-headless` and NumPy; do not install a second OpenCV package alongside it. Existing Python databases need no manual schema changes.

Detection renders one visible PDF page per request at up to 200 DPI, capped at 12 million pixels / 5,000 pixels per side. Completed pages and suggestions are persisted in SQLite. Retrying resumes from the first unfinished page. The UI processes pages sequentially; without JavaScript, click **Resume detection** for each subsequent page.

Suggestions and saved regions are separate. Detection and **Clear suggestions to rerun** preserve your saved crops and extracted rows. Accepting or changing crops resets row extraction; repeating an unchanged approval does not. Accept/edit/remove actions check revisions to prevent stale changes. Run extraction again after crop changes, then refresh and review the equipment log.

The supplied detector uses ruling lines, enclosed cells, and connected line networks. It targets upright ruled schedules and is not OCR or a borderless-table model. Faint, skewed, or dense drawings may need manual selection. A heuristic removes narrow right-edge title blocks, which can also hide legitimate tables there. Detection adds 18 points above a table and 4 points on other sides for headings; review this padding before extraction. Large sheets are downscaled, which may lose fine lines. Per-page processing stops above 2,500 cells, and each drawing supports up to 200 suggestions/saved regions; split dense PDFs or use manual crops.

See [TABLE-DETECTION.md](TABLE-DETECTION.md) for the API, source integration, and acceptance checks.

## Deployment

The same Python app can target Vercel with remote Turso/R2, or a persistent Docker host. The Docker path is the fallback if native processing exceeds Vercel limits.

```bash
docker compose up --build -d
```

The included configuration binds to localhost and persists SQLite and local objects in a named volume. For a hosted deployment, set `APP_ORIGIN` to the exact public HTTPS origin, put a TLS reverse proxy in front of port 8000, preserve the incoming Host header, and attach persistent storage at `/app/data`. Secure cookies are enabled when `APP_ORIGIN` is HTTPS. The included Gunicorn configuration uses separate synchronous workers for PyMuPDF. The development server is for local use.

R2 is optional. Configure all R2 values in `.env` to reuse private remote objects; Turso is used when TURSO_DATABASE_URL and TURSO_AUTH_TOKEN are set; otherwise SQLite remains local. See [MIGRATION.md](MIGRATION.md) before using existing production data. Keep the settings secret stable so previously saved keys remain decryptable. Create the owner account before opening a fresh deployment to other users.

## Code map

| Path | Responsibility |
| --- | --- |
| `builderp/__init__.py` | App setup, security checks, JSON API |
| `builderp/views.py`, `templates/`, `public/static/` | Python-rendered pages and small browser enhancements |
| `builderp/store.py`, `schema.sql` | SQL, object storage, transactional state, processing leases |
| `builderp/auth.py`, `ai.py` | Accounts, encrypted settings, Gemini REST calls |
| `builderp/domain.py`, `catalog.py` | Evidence validation, specification structure, product classification |
| `builderp/services.py` | Documents, submittals, review, indexing, chat |
| `builderp/pdf.py`, `drawings.py`, `equipment.py` | PDF operations, reviewed crop workflow, equipment mapping |
| `builderp/table_detection/`, `table_jobs.py` | OpenCV line/cell/table pipeline, bounded rendering, resumable detection, explicit approval |
| `builderp/turso.py`, `cloud_uploads.py` | Turso HTTP transactions, authenticated R2 direct uploads |
| `app.py`, `vercel.json`, `migrate.py` | Vercel entrypoint, deployment configuration, explicit schema setup |
| `builderp/exports.py` | Excel, Markdown, and JSON registers |
| `python_tests/` | Integration and domain regression tests |

## Verification

```bash
python -m unittest discover -s python_tests -v
python -m compileall -q builderp run.py
```

Tests use real temporary SQLite databases and generated PDFs. External AI responses are mocked, so tests need no API key and incur no AI charges. Live Gemini, R2, and production hosting require separate checks with your configuration. Existing encrypted API keys and password hashes remain compatible, but this is a runtime migration; the new UI and upload API differ from the former app.
