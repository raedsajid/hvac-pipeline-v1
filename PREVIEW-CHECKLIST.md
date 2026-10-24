# Preview setup checklist (this machine)

Python app lives here (separate from Next.js):

`C:\Users\raeds\Desktop\vercel-spec\BuildERP-vercel-ready-2026-09-29\BuildERP-python`

Next.js production repo was **not** modified for this import.

## Already done locally

- [x] ZIP extracted into this folder on git branch `python-preview`
- [x] Initial commit created
- [x] `.env` created with local `SETTINGS_SECRET` / `SECRET_KEY` (Turso/R2 left empty → local SQLite)
- [x] `python migrate.py` → `BuildERP schema initialized; existing records retained.`
- [x] Unit tests mostly green (96 passed; 1 Windows temp-DB cleanup error)

Note: machine has Python **3.11**; ZIP prefers **3.12**. Install 3.12 before Vercel parity if possible. Vercel will use 3.12 from `.python-version`.

## You must create (do not use production)

### A. Test Turso database

1. https://turso.tech → create DB e.g. `builderp-python-preview`
2. Or clone from a **backup** of production if you want existing accounts
3. Copy `libsql://…` URL + auth token

### B. Private test R2 bucket

1. Cloudflare → R2 → create bucket e.g. `builderp-python-preview`
2. Create API token scoped to that bucket
3. CORS (after you know the Vercel URL):

```json
[
  {
    "AllowedOrigins": ["https://YOUR-PREVIEW.vercel.app"],
    "AllowedMethods": ["PUT", "GET", "HEAD"],
    "AllowedHeaders": ["Content-Type"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3600
  }
]
```

4. Lifecycle expire **only** `python-staging/` and `python-downloads/` after 1 day — never expire `pdf/` or `cache/`

### C. Fill local `.env` for Turso migrate (optional before Vercel)

Edit `.env` (already gitignored):

```
TURSO_DATABASE_URL=libsql://builderp-python-preview-….turso.io
TURSO_AUTH_TOKEN=…
R2_ACCOUNT_ID=…
R2_ACCESS_KEY_ID=…
R2_SECRET_ACCESS_KEY=…
R2_BUCKET=builderp-python-preview
SETTINGS_SECRET=…   # original if copying encrypted Settings; else keep local value
SECRET_KEY=…        # keep stable
```

Then:

```powershell
.\.venv\Scripts\Activate.ps1
python migrate.py
```

## Separate Vercel project (critical)

1. Push this folder to a **new GitHub repo** (or a remote that is **not** the Next.js production branch)
2. Vercel → **Add New Project** → import that repo  
   - Framework: **Flask**  
   - Root: directory with `app.py`  
   - Clear any Next.js build/output overrides  
   - Python 3.12  
   - Fluid Compute; `vercel.json` already sets 300s
3. Set **Preview** env vars from the table in `DEPLOY-VERCEL.md` (test Turso/R2 only)
4. Deploy → open the preview URL → set R2 CORS to that exact origin
5. Optionally set `APP_ORIGIN` to that exact URL (no trailing slash)

## Verify after deploy

1. Register first account (becomes owner) **or** sign in with copied test user
2. Settings → **Run deployment check** → expect `ok: true`, `database: turso`, `storage: r2-configured`, OpenCV/numpy/pymupdf versions, `syntheticTables: 1`
3. Local smoke (from this folder, with preview URL):

```powershell
$env:SMOKE_URL = "https://YOUR-PREVIEW.vercel.app"
$env:SMOKE_USERNAME = "…"
$env:SMOKE_PASSWORD = "…"
.\.venv\Scripts\python.exe scripts\smoke_deployment.py
```

4. Browser full flow: upload drawing → Detect tables → accept crops → Extract → equipment log → export

## Local app (no Turso) for UI peek

```powershell
.\.venv\Scripts\Activate.ps1
python run.py
```

Open http://127.0.0.1:8000 — uses local SQLite under `./data` until Turso is set.
