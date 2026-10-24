# Deploy the Python BuildERP app on Vercel with Turso and R2

This release is prepared for a **preview deployment**. It has not been deployed to your Vercel account or exercised against your production credentials. The native detector and live integrations must pass the checks below before replacing production.

## What changed

- Flask + Jinja remain the single application. OpenCV runs in that Python process; no Next.js runtime or detector sidecar is required.
- `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` select a remote database through Turso's SQL-over-HTTP protocol. There is no local replica or temporary SQLite production database.
- Existing R2 environment names and `pdf/<sha256>` object keys are retained. New browser uploads go directly to a private staging object in R2, then the server validates the PDF and registers it. This preserves the 10 MB document limit without sending a 10 MB body through a Vercel Function.
- Original PDF links redirect to short-lived signed R2 downloads after ownership checks. Generated responses over 4 MB also use a private signed object download.
- Gemini still uses the Settings screen's encrypted key or `GEMINI_API_KEY`. Preserve the original `SETTINGS_SECRET` to decrypt existing saved keys.
- `app.py`, `vercel.json`, `.python-version`, and `public/static/` provide the Flask deployment entrypoint and static assets.
- Startup does **not** migrate a remote database. Schema initialization is an explicit `python migrate.py` step.
- Existing Python UI/workflows are retained. The original Next.js reference was reviewed for workspace, specifications, products, crop review, equipment, and settings flows; this Jinja interface is not a pixel-identical React port. Browser visual parity remains an acceptance check.

## 1. Prepare a preview branch and test data

Create a branch from the existing repository. Preserve the production commit/tag and existing Vercel project settings for rollback. Copy the **contents** of the ZIP's `BuildERP-python/` folder to the branch root (or select that subdirectory as the Vercel Root Directory).

Remove the old Next.js runtime files/configuration from this branch's deployment root: `package.json`, Next configuration, old `app/` API routes, and obsolete build commands. Keep them recoverable in Git. Do not copy `.env`, virtual environments, SQLite databases, or credentials into Git.

Use a Turso test database cloned from a backup of production and a private test R2 bucket containing the required PDF/cache objects. Do not connect both versions as simultaneous writers to production. Preview changes include user actions, processing state, and schema additions.

## 2. Set environment variables

Set these in the Vercel project's **Preview** environment and in a private local `.env` used for migration. Use the corresponding production values only at cutover.

| Variable | Value |
| --- | --- |
| `TURSO_DATABASE_URL` | Test database's `libsql://…` or HTTPS URL |
| `TURSO_AUTH_TOKEN` | Token for that test database |
| `R2_ACCOUNT_ID` | Cloudflare account ID |
| `R2_ACCESS_KEY_ID` | Private bucket access key |
| `R2_SECRET_ACCESS_KEY` | Private bucket secret |
| `R2_BUCKET` | Test bucket name |
| `SETTINGS_SECRET` | Original value if testing copied encrypted Settings records |
| `SECRET_KEY` | Stable randomly generated Flask session secret, shared across instances |
| `APP_ORIGIN` | Exact HTTPS URL you will visit, without trailing slash |
| `GEMINI_API_KEY` | Optional fallback if no key is saved in Settings |
| `GEMINI_CHAT_MODEL` | Existing model choice, or `gemini-2.5-flash` |
| `EMBEDDING_MODEL` | Existing model choice, or `gemini-embedding-001` |

Generate a new SECRET_KEY with `python -c "import secrets; print(secrets.token_hex(32))"`. Do not regenerate SETTINGS_SECRET when reusing encrypted keys. All credentials are server-side; no NEXT_PUBLIC variables are needed.

For preview deployments, APP_ORIGIN can be omitted: Vercel's automatic VERCEL_URL is used. Visit that exact deployment URL. For a stable branch URL or custom domain, set APP_ORIGIN to that URL and visit it. Do not use the production origin in Preview. The app deliberately rejects other hosts and cross-origin mutations. Do not manually set VERCEL locally.

## 3. Configure private R2 uploads

In the test bucket, configure CORS with the exact preview origin (and later the production origin). Keep the bucket private. Example Cloudflare dashboard CORS configuration:

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

Merge this into any existing rules rather than removing required production rules. A different preview hostname needs its own allowed origin. Signed URLs use the S3 endpoint, not a public/custom bucket URL. Browser upload requests use `Content-Type: application/pdf`.

Grant the server token object read/write/delete access to the selected bucket. Add lifecycle expiration rules of one day for **only** `python-staging/` and `python-downloads/`. These prefixes contain abandoned upload staging and temporary generated downloads. Never expire `pdf/` or `cache/`. Periodically delete expired rows from `python_uploads` as maintenance; do not delete active tickets.

## 4. Install, migrate the TEST database, and test

Use Python 3.12 in a virtual environment:

```bash
python -m pip install -r requirements.txt
python migrate.py
python -m unittest discover -s python_tests -v
```

`migrate.py` retains existing records and creates missing schema/indexes and the Python job/object/upload tables. Run it once during a controlled deployment window, not concurrently on multiple hosts. Back up the database before migration. Do not run this against production until preview acceptance is complete.

No OpenCV tests should be skipped in your installed environment. The GitHub Actions workflow explicitly imports OpenCV before tests. For remote data compatibility, sign in using an existing test-copy account and compare existing projects, requirements, catalog, PDF links, and equipment decisions.

## 5. Deploy the preview

In Vercel, use the Python branch as a preview, or create a separate preview project from that branch if changing framework settings would affect the existing Next.js production project.

- Framework: **Flask** (the included manifest declares it).
- Root Directory: the directory containing `app.py` and `requirements.txt`.
- Clear old Next.js Build Command, Install Command, and Output Directory overrides; use the Flask defaults.
- Python: **3.12** (`.python-version` is included).
- Use Fluid Compute. The included function configuration sets `maxDuration` to 300 seconds.
- Do not use a `next build`, `.next` output, or a Gunicorn start command on Vercel. Docker/Gunicorn remains the alternative hosting path.

Inspect build and runtime logs. Record the dependency install result, bundle size, cold-start errors, native import results, detection duration, and memory usage. Do not treat a green build as proof that native image processing works.

## 6. Run the native deployment check

Sign in as the owner, open Settings, and click **Run deployment check**. The owner-only `/api/deployment-check` endpoint checks database access, imports the installed OpenCV/NumPy/PyMuPDF, renders a generated grid PDF, and requires exactly one detected table. It makes no Gemini calls and does not modify user data.

Then use the included smoke script from your local installed environment. Set `SMOKE_URL`, `SMOKE_USERNAME`, and `SMOKE_PASSWORD` through your shell/environment (do not commit them), then run:

```bash
python scripts/smoke_deployment.py
```

For protected previews, optionally set `VERCEL_AUTOMATION_BYPASS_SECRET` using Vercel's authorized automation bypass configuration. The script signs in, checks native detection, creates a disposable project, uploads a generated PDF to R2, detects and accepts its table, and deletes the project. It may leave a content-addressed sample PDF in R2. It does not change saved API keys.

Set `SMOKE_GEMINI=1` only if you want the script to make billable Gemini calls to test generation, embeddings, and schedule extraction. This uses your existing configured key. Synthetic blank table extraction is a connectivity test, not an accuracy assessment.

## 7. Test the full real-document flow in a browser

- Sign in with a copied existing account; check Settings key decryption.
- Upload native and scanned specification PDFs, including one between 4.5 and 10 MB. Confirm the network upload is a direct R2 PUT and the PDF opens.
- Prepare the specification (including real OCR), generate submittals, inspect source evidence, review/approve/reject requirements, and compare the product catalog with the source.
- Attach products and create combined/individual drafts. Retry a stale review from another tab and confirm it is rejected.
- Prepare chat, ask source-grounded questions, and inspect citations.
- Upload drawings with actual ruled schedules: native, scanned, rotated, existing CropBox offsets, adjacent tables, faint lines, and large sheets.
- Detect tables, leave/reload mid-job, resume, inspect highlights, accept only intended schedules, edit one crop, and add/remove a manual crop.
- Extract real schedule rows, generate the equipment log, review CSI choices, and confirm saved decisions survive rebuilding.
- Export JSON, Markdown, and Excel. Check approved export gates, source values, and downloads over 4 MB if your data produces them.
- Check desktop/mobile navigation, crop dragging, upload errors, and progress messages. Existing workflows are preserved, but the new UI needs this visual acceptance pass.
- Confirm another user cannot access the project's PDFs, upload tickets, detections, or reviews.
- Redeploy/restart and confirm persisted state and signed-in access remain usable.

## Vercel fit and fallback

Vercel supports Flask/Python, and this pipeline does not inherently require a separate service. However, no native Vercel benchmark was run during packaging. Current documented limits include 4.5 MB function request/response bodies, a standard Python bundle limit of 500 MB, and a 300-second maximum on Hobby with Fluid Compute. Larger bundles/longer durations have plan/runtime conditions; inspect your account rather than assuming availability.

The pipeline renders at most 12 million grayscale pixels, caps assembly at 2,500 cells, and advances one page per request. The application serializes requests per Python process because PyMuPDF is not thread-safe; Vercel can scale processes. Heavy concurrent jobs may benefit from a dedicated worker architecture later.

Turso's HTTP interactive transactions have a documented short transaction window (5 seconds). The adapter retains one stream, queues non-result writes into conditional batches, keeps AI/native processing outside transactions, and does not replay writes after uncertain network failures. Place compute close to the database. Large finalization/review operations still need a real latency/load test; a timeout fails the transaction and surfaces an error. Local protocol-emulator tests do not prove latency tolerance.

If native imports fail, the dependency bundle exceeds your available limit, or representative sheets repeatedly exceed CPU/memory/duration limits, deploy the **same full Python app** on a Docker-capable host with the included Dockerfile. Use a Python web service on a container host or a VPS, retain the Turso/R2 environment variables, and run Gunicorn with synchronous workers. This keeps one Python stack and avoids reintroducing a Next.js/OpenCV sidecar. Turso transaction latency must still be tested on that host.

## Production cutover and rollback

After preview passes: back up Turso, preserve the current Git/Vercel deployment, stop old-app writes, run the additive migration against production once, configure production secrets/origin and R2 CORS, then deploy the tested Python commit. Confirm sign-in, one upload, native detection, one extraction, and an export before reopening normal use.

The old Next.js app does not read all Python-specific caches. Redeploying the old code is not a complete data rollback after Python writes. Restore the coordinated database snapshot if reverting and account for any new PDF objects. Keep production isolated until the cutover decision.

## References checked for this release

- https://vercel.com/docs/frameworks/backend/flask
- https://vercel.com/docs/functions/limitations
- https://docs.turso.tech/sdk/http/reference
- https://developers.cloudflare.com/r2/api/s3/presigned-urls/
