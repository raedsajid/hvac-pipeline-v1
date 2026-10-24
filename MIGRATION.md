# Python migration notes

For the current Turso/R2 Vercel migration, follow **DEPLOY-VERCEL.md**. The local snapshot path below is only for SQLite development or a local-host cutover. Do not point a preview at the writable production database.

## Scope and deployment boundary

This branch replaces the JavaScript runtime with a single Python service. The former Next.js app and drawing-detect sidecar remain recoverable from Git history at `56a40aee416675c5b6591e70f6141a59209f8f4b`; they are not part of the Python startup path. Do not merge or change production traffic until your own PDFs and configured integrations pass acceptance checks.

No production database, bucket, API key, or deployed service was accessed or changed during implementation. Cloud adapters are tested against local protocol emulation and mocks, not live credentials.

| Previous component | Python replacement |
| --- | --- |
| Next.js API route | Flask JSON routes in `builderp/__init__.py` |
| React workspace | Jinja pages and ordinary HTML forms |
| PDF.js/unpdf/pdf-lib processing | PyMuPDF text, page rendering, and cropping |
| Turso/libSQL client | Turso over HTTPS in cloud deployments; compatible local SQLite for development |
| R2 object storage | Local private files by default; optional R2 adapter |
| TypeScript Gemini client | Python REST client and compatible AES-GCM key encryption |
| OpenCV sidecar | In-process OpenCV detection in the Python service |
| Browser-issued R2 upload URLs | Authenticated multipart upload to the Python service |
| TypeScript domain tests | Python integration and domain regression tests |

The interface has been rebuilt, not reproduced pixel for pixel. Browser-only enhancements (processing loops, filtering, crop dragging) use a small JavaScript file; page rendering and application logic are Python. No JavaScript build tool or Node service is needed.

## Existing data

Use a **copy** of existing data for validation. The application does not automatically copy or modify your Turso production database.

1. Export a consistent SQLite snapshot of your existing Turso database using your normal database backup/export process. Keep the original untouched.
2. Stop the Python app. Copy that SQLite snapshot to `<BUILDERP_DATA_DIR>/builderp.sqlite3`. Never replace a database while a server is using it.
3. Set the original `SETTINGS_SECRET` if reusing the owner's encrypted Gemini key. The Python application supports the same `base64(iv).base64(ciphertext)` AES-GCM format and PBKDF2-SHA256 password records.
4. Either configure the original private R2 bucket using the R2 environment variables, or copy its objects into `<BUILDERP_DATA_DIR>/objects/` with the exact original keys. PDFs live at `pdf/<sha256>`; parsed evidence and other legacy caches use their existing `cache/...` paths. A database backup alone does **not** include the PDFs or cached evidence.
5. Start the Python app. It creates missing tables and compatibility columns, and adds `python_jobs` and `python_objects`. Existing users, projects, reviews, and evidence IDs are retained.
6. Open representative projects, validate source quotes against page previews, compare exports, and test sign-in with existing accounts. Do not start generation on an imported document until its evidence objects are available. If evidence is unavailable, review approval is blocked.

New mutable caches and per-document product decisions are stored transactionally in `python_objects` in the selected SQL database. Originals remain content-addressed in local files or R2. Back up the SQLite database **and** object store together. The old TypeScript service does not read newly generated Python caches, so switching back is a restore-to-snapshot operation, not a bidirectional migration. Do not run the old and new applications as simultaneous writers to the same data.

## Preserved rules

- Per-user project ownership on document, review, chat, and export paths.
- Password hash and encrypted AI key compatibility; sessions stored only as token hashes.
- Same-origin mutation checks and CSRF tokens on HTML forms.
- Persistent authentication, upload, and AI usage limits.
- Content-addressed PDFs with original 10 MB / 200-page limits.
- Evidence-linked extraction, OCR warnings, section/article ownership, checklist exclusions, and submittal-region filtering.
- Source-backed product classification, scope alias consolidation, reviewer catalog placements, and individual/combined product drafts.
- Review notes for warnings/rejections, explicit approval confirmation, and revision checks.
- Resumable batched extraction/indexing and bounded provider timeouts.
- Document-scoped chat with validated citation labels.
- Draft and approved Excel/Markdown/JSON exports; all source values are written as text in Excel.

## Deliberate changes

- Turso is the active database when its URL/token are set. Vercel requires Turso and R2; local development can use SQLite and local files.
- With R2 configured, the browser requests `upload-url`, PUTs directly to private R2 staging, and calls `upload-complete`. Server-side PDF validation precedes registration. Multipart `upload` remains available for local clients; Vercel limits its payload to 4.5 MB.
- OpenCV detects ruled table candidates locally. Explicitly accept suggestions or add manual crops, then use Gemini to transcribe the saved schedule regions. Detection and row extraction are separate steps. Existing manual crops remain valid.
- New crop state uses per-project document IDs, and saving changed crops resets that drawing's extracted rows. Equipment review decisions are retained when rebuilding proposals; inspect stale equipment when changing or removing crop regions.
- Equipment edits require a revision number; confirmed/rejected items survive a rebuild.
- Existing data is not automatically migrated. Deployment secrets and provider keys must be supplied by the owner.

## Acceptance checks before cutover

- Create a disposable member account and verify it cannot access another user's project or PDF.
- Upload native-text, scanned, and rotated PDFs, including one over 4.5 MB.
- Prepare, generate, interrupt, and resume a representative specification. Compare obligations and products with the source and the old app; AI output can differ between runs.
- Test every catalog classification relevant to your projects, including materials, integral components, prohibited items, and aliases.
- Approve a requirement, try a stale edit, and confirm the approved export matches your review.
- Test real Gemini OCR, embeddings, and cited chat with the selected models and your key.
- If using R2, test retrieval of an original PDF and its parsed evidence with the configured bucket.
- Detect tables on native, scanned, rotated, and cropped PDFs. Inspect borders; accept/edit/remove candidates, add manual crops, interrupt/resume detection, and rerun it. Extract schedule rows and review the resulting equipment log.
- Restart the service/container and confirm accounts, PDFs, jobs, and reviews persist.

Automated tests use mocked provider responses; they do not demonstrate live AI quality, remote storage credentials, or production deployment readiness.
