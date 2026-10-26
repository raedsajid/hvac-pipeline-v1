# BuildERP UI parity

The app stays on Flask/Jinja with plain CSS and JavaScript. No React build, Next.js runtime, detector sidecar, or new frontend dependencies are required.

## Reference and verification status

Implemented from the eight desktop screenshots supplied in this conversation and the original Next.js `app/globals.css`, `components/brand.tsx`, workspace, crop-review, and equipment-log components. The original graphite/orange palette, Arial typography, document logo, sidebar proportions, spacing, cards, compact document lists, and labels are reused.

**This is a reference-based UI implementation, not a certified pixel match.** Chromium is unavailable in this environment, so rendered side-by-side comparison and browser interaction checks remain outstanding. No mobile screenshots were supplied. Crop review, equipment, Settings, populated tables, and edit screens are component-based completions because finished screenshots were unavailable.

| Screen | Implemented | Remaining difference / check |
| --- | --- | --- |
| Login / signup | Split graphite/white layout, original logo, headline, orange sign-in action, field labels, account switch | Signup follows the login reference. Back to BuildERP returns to the app entrypoint; this ZIP does not add a marketing landing page. |
| Workspace / projects | Persistent sidebar, current-project dropdown, breadcrumb header, three metrics, project library cards, original footer, New project dialog | Project totals are real data. Browser spacing/font comparison remains to be done. |
| Project / specifications | Original project-tool navigation, compact shared-border document list, upload dialog, status badges, four-step order | Processing remains explicitly started/resumed, as in the Python ZIP. It does not automatically run Gemini after upload. Labels describe this accurately. One PDF per upload. |
| Submittal log | Original heading, search/status/sort controls, selected-row toolbar, format chooser, draft and selected exports, empty state, source links | Populated layout follows source components; no populated screenshot was supplied. Generate opens the document chooser, then uses the existing per-document processing actions. Approved export gates are retained. |
| Source review | Document chooser, gray PDF pane, original-PDF link, Submittals/Products switch, search, source highlighting on card hover/focus | Uses one server-rendered page at a time with page controls, rather than continuously rendering an entire PDF. Source-text disclosure remains available. |
| Product / requirement editing | Original palette, typography, borders and controls, source beside the editable obligation, product selection, revision/approval fields | Requirement editing is a full route with a two-column editor, rather than the original React drawer. Product catalog uses grouped rows. No completed edit-panel screenshot was supplied. |
| Document chat | Focused document selector, original empty-state composition, suggested prompts, sticky composer, Enter/Shift+Enter, citations | Answers use the existing form request and page reload; no streaming was added. Preparation is explicit. |
| Activity | Bordered activity list, clock icons, separators, local browser timestamps | Events use existing Python event messages. |
| Drawings / crop review | Red numbered canvas boxes, page selection, detection progress, inspect/accept/reject controls, saved-region editing, manual drawing, extraction action | Dedicated page rather than a React dialog. Edit a saved region by redrawing or numeric coordinates; drag handles for moving/resizing existing boxes are not implemented. Rejection is an undoable browser-tab decision, not a new persisted backend state. Saved/accepted crops remain authoritative. |
| Equipment log | Drawing list with Review crops links, Upload drawings, generation action, review table, CSI/status forms, draft CSV download | Completion based on original components and existing Python fields. CSV is generated in the browser from displayed equipment records; it is a draft. No finished screenshot was supplied. |
| Settings | Same app shell and controls, AI connection, encrypted key form, password change, owner deployment-check link | Completion based on the existing component styling. API behavior, owner permissions and environment names are unchanged. |
| Mobile | Collapsible navigation, stacked review panes, responsive cards/forms, horizontally scrollable data tables | Adapted from the original desktop design; no mobile screenshots or browser validation were available. |

## Behavior retained

- OpenCV detection remains in the Python process. Acceptance, editing, extraction, and revision checks use the existing endpoints.
- Turso/R2 adapters, object keys, migrations, direct-upload handshake, limits, and Gemini services are unchanged.
- `TURSO_*`, `R2_*`, `SETTINGS_SECRET`, `SECRET_KEY`, `APP_ORIGIN`, and Gemini settings retain their existing names and meaning.
- Existing form actions and API contracts remain intact. Old `#register` / `#equipment` redirects are routed to the corresponding sidebar page by the browser.
- Settings → Run deployment check and `scripts/smoke_deployment.py` are unchanged.
- JavaScript supports dialogs, mobile navigation, filtering, crop overlays, and direct R2 uploads. Normal application use should have JavaScript enabled.

## Checks completed here

- 105 Python tests passed; four native OpenCV tests skipped (109 total).
- This includes 12 new UI integration tests across local SQLite and the Turso protocol emulator: page rendering, populated source/products/chat/review views, cloud-upload form wiring, authorization, and drawing accept → edit → extract → equipment forms.
- Real PDF rendering/cropping is exercised; detector output and Gemini responses are mocked in the workflow tests. R2 is mocked and Turso uses its existing local HTTP-protocol emulator.
- Python compilation and JavaScript syntax checks passed. No browser-rendered visual or interaction pass was possible.

## Preview acceptance still required

Use a **separate Vercel project**, test Turso database and private test R2 bucket, following `DEPLOY-VERCEL.md`.

1. Compare login, workspace, specifications, submittal log, source, chat and activity beside the supplied screenshots at the same browser zoom and content viewport size.
2. Check New project and Upload PDFs dialogs, project switching, search/status/sort, row selection and draft/selected exports.
3. Upload a 4.5–10 MB PDF; verify direct R2 PUT, successful completion and original-PDF access.
4. Detect a real drawing. Verify red boxes align at desktop and narrow widths. Inspect, reject/undo, accept, edit and save a crop; navigate away and confirm saved crops persist. Rejections are intentionally local to the browser tab/detection revision.
5. Extract a schedule with the configured Gemini connection; build the equipment log, save a CSI decision and regenerate. Check draft equipment CSV and existing register exports.
6. Test source highlights, product editing and chat citations; try a stale review in another tab.
7. As owner, run Settings → Run deployment check and require `ok: true`; then run the existing smoke script. Native OpenCV, live Turso/R2, Gemini and Vercel execution have not been verified here.
8. Check keyboard focus, Escape on dialogs/navigation, crop coordinates without dragging, and a narrow mobile viewport.

No deployment or production data changes were made while producing this ZIP.
