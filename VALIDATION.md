# Migration validation

Verified on Python 3.12 with temporary SQLite databases and generated PDFs:

- 93 Python integration/domain tests passed; 4 native OpenCV tests were skipped (97 total, run in two groups) (`python -m unittest discover -s python_tests -v`).
- Python modules compiled successfully.
- Browser enhancement JavaScript passed `node --check public/static/app.js`.
- All Jinja templates compiled and primary authenticated pages rendered in integration tests.
- An actual local HTTP server passed health, account creation, authenticated project creation, workspace, settings, and project-page checks.

Coverage includes tenant isolation, CSRF/origin checks, compatible authentication, session invalidation, encrypted key round trips, PDF validation/reuse, rotated coordinates, OCR, source structure, resumable processing, batch splitting, missing-evidence migration safeguards, evidence validation, stale review rejection, product classification/alias merging, product draft grouping, chat citations, manual drawing crops, equipment mapping and review preservation, and JSON/Markdown/Excel exports. Formula-like source values remain plain strings in Excel.

External AI responses were mocked. No live Gemini key, Turso database, R2 bucket, or production deployment was used.

Package downloads were unavailable in the execution environment. Flask and its pure-Python dependencies were tested from their official tagged source releases matching `requirements.txt`; PyMuPDF 1.26.6, cryptography 50.0.2, and openpyxl 3.1.5 were provided by the runtime. A clean `pip install -r requirements.txt`, optional boto3/R2 integration, the Docker build, and Gunicorn startup still need verification in a network-enabled deployment environment.

The new detection workflow was tested with mocked detector outputs and real PDF rendering/cropping. OpenCV could not be installed in this environment because package downloads were blocked. The supplied native algorithm therefore remains unverified here; install requirements.txt and run the four native detector tests before relying on detection results. The GitHub Actions workflow includes a required OpenCV import check, but was not run here.

Browser automation could not run because no Chromium executable was installed. Desktop/mobile visual layout, browser file selection, drag-to-crop interaction, and JavaScript-driven processing loops need a browser acceptance pass. The processing form endpoints can still advance one batch per submission. The restyled interface uses JavaScript for dialogs, mobile navigation, canvas overlays and direct uploads.

See `MIGRATION.md` for cutover checks and existing-data handling.


## Turso/R2/Vercel adaptation

The 42 cloud tests run the app's existing workflows and detection orchestration through a local SQLite-backed Hrana HTTP protocol emulator, plus failure/rollback and direct-upload tests. R2 is mocked. These verify adapter behavior and integration, not live Turso transaction latency, R2 CORS, or Vercel deployment. Test configuration explicitly disables environment-selected cloud databases so tests cannot accidentally use deployment credentials from .env.

A real local HTTP server served health, login, JavaScript, and CSS successfully after the static asset migration. Python compilation and JavaScript syntax checks passed. Native OpenCV remains unavailable in this runtime; four native tests are explicitly skipped. The deployed owner-only check and smoke script are provided to close that gap in Vercel.

Not verified: a clean install of the complete requirements, Vercel build/bundle compatibility, actual native OpenCV execution, real Turso/R2 calls, Gemini responses, and browser interaction/visual parity. No production deployment was attempted or modified. See DEPLOY-VERCEL.md for the mandatory preview acceptance workflow and fallback.


## UI parity update

After the UI update, 105 tests passed and 4 native OpenCV tests were skipped (109 total). The 12 added UI tests run six scenarios on SQLite and the existing Turso HTTP-protocol emulator. They cover template compilation and rendering, populated source/product/chat/review screens, project navigation, preserved direct-upload form wiring, output escaping and authorization, and drawing detection proposals → accept → edit → extract → equipment review. Detection and Gemini outputs are mocked; PDF operations are real.

Python compilation and JavaScript syntax checks passed. No Chromium executable is installed, so no pixel-level comparison, browser interaction, responsive screenshot, or live Vercel acceptance is claimed. `UI-PARITY.md` records the exact remaining checks and differences. Deployment-check, smoke script, storage adapters, schema, API dispatch, configuration and processing modules are byte-for-byte unchanged from the previous Python ZIP.
