# UI update — changed files

Compared with the previous Python Vercel ZIP.

## UI files

Updated:

- `public/static/app.css` — original BuildERP palette, typography, shell and page styles; responsive layouts.
- `public/static/app.js` — sidebar/dialogs, filtering/sorting/selection, document switching, source highlights, chat prompts, red crop overlays, local reject/undo and draft equipment CSV. Existing upload URLs/payloads and processing endpoints are retained.
- `builderp/templates/base.html` — sidebar, project switcher, breadcrumb header and footer.
- `builderp/templates/login.html` — original split login composition.
- `builderp/templates/workspace.html` — project library and creation dialog.
- `builderp/templates/project.html` — separate project-tool screens and upload/delete dialogs.
- `builderp/templates/document.html` — document selection, source findings and product layout.
- `builderp/templates/drawing_review.html` — red crop review, progress and review controls.
- `builderp/templates/review.html` — editor layout and register navigation.
- `builderp/templates/settings.html` — shared visual styling; deployment check shown to owners.

Added template partials:

- `builderp/templates/_icons.html`
- `builderp/templates/_upload.html`
- `builderp/templates/_register.html`
- `builderp/templates/_equipment.html`
- `builderp/templates/_document_workflow.html`
- `builderp/templates/_chat.html`

## Python presentation changes

Only `builderp/views.py` changed:

- Supplies the signed-in user's project list to the sidebar.
- Adds a date-formatting template filter.
- Selects the project tool using `?tab=…`; source/chat links open an available document.
- Supplies the current project's document choices and the current document's requirements for source findings.

These are navigation and read-only presentation changes. Existing POST handlers, API routes, authorization, schemas, migrations, AI processing, detection, crop persistence, extraction, Turso/R2 access, upload handling, configuration and exports were not changed. No new backend dependency was added.

## Tests and documentation

- Added `python_tests/test_ui.py`: six UI integration scenarios run against both local SQLite and the existing Turso emulator.
- Added `UI-PARITY.md` and this `UI-CHANGES.md`.
- Updated `VALIDATION.md`, `START-HERE.txt`, and the UI/separate-preview-project wording in `DEPLOY-VERCEL.md`. Deployment commands and environment requirements remain the same.

The ZIP excludes test databases, credentials, virtual environments, cache files, reference screenshots and working reference sources.
