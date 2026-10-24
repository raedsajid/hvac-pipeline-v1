# Table detection integration

The four modules supplied in `Table detection.zip` are integrated in `builderp/table_detection/`. Imports now use the BuildERP package; the line detector also rejects empty images and nonpositive kernel divisors. The original ruling-line, cell-filtering, table-assembly, orphan-recovery, and title-block-filtering algorithms are retained.

`builderp/table_jobs.py` connects the detector directly to the Flask application. There is no sidecar, detector HTTP service, Node runtime, or frontend build.

## Workflow

1. Upload a **drawing** PDF. Text preparation and a Gemini key are not prerequisites for detection.
2. Open **Table & crop review**, then **Detect tables**. Progress is saved after every page; retry resumes from the first unfinished page.
3. Click **Inspect** next to a suggestion to see its highlighted border. Tick the desired schedules and **Accept selected tables**. Unselected suggestions are not extracted.
4. Use **Edit** to drag new bounds or enter coordinates. Include the schedule title and all column headings. You can also add a manual crop or remove a saved region.
5. Click **Extract schedule rows**. This step uses Gemini. Return to the project to generate/refresh and review the equipment log.

The saved-region overlay and crop coordinates use the visible page orientation. PDF extraction accounts for rotation and existing CropBox offsets. Suggestions add a small amount of padding for titles and borders; inspect nearby content before accepting.

## Persistent state and concurrency

- Suggestions: `python/table-detection/<document-id>.json` in the configured SQL database (`python_objects`).
- Reviewed crops: the existing `python/crops/<document-id>.json`.
- Extracted rows: the existing `python/schedule-rows/<document-id>.json`.
- Job kind: `detect-tables` in `python_jobs`, one page per request.
- Page outputs commit atomically with the detection cursor. Errors leave earlier pages intact. A worker whose lease expired cannot commit stale results.
- Detection uses project-document IDs and ownership checks. Accepting candidates checks both detection and crop revisions. Crop changes are blocked while row extraction holds a valid lease.
- Candidate IDs derive from page number and table coordinates. Repeated approval preserves edited crop coordinates and extracted rows.
- Clearing suggestions increments the detection revision and resets only the detection job. Accepted crops, manual crops, and extracted rows remain untouched.
- Changing accepted/manual crops resets schedule rows. Existing equipment review decisions are retained by the equipment workflow; inspect the equipment log after changing source crops.

## API

All routes below are scoped to an authenticated owned drawing. POST requests require the configured `Origin` header. HTML equivalents use session CSRF tokens.

| Method | `/api/documents/<id>/…` | Behavior / JSON input |
| --- | --- | --- |
| GET | `table-detection` | Returns `proposals`, `page_results`, `revision`, `cursor`, `pages`, `progress`, `done`, `busy`, and `error` |
| POST | `prepare-drawing-crops` | Processes one remaining page; `{}`. Repeat until `done` |
| POST | `accept-tables` | `{"ids":["detected-…"],"revision":0,"detection_revision":1}`; returns reviewed crop state |
| POST | `reset-table-detection` | `{"revision":1}`; clears suggestions only |
| GET | `crops` | Existing reviewed crop state |
| POST | `crops` | Existing API: `{"revision":0,"crops":[{"id":"…","page":1,"box":[0.1,0.1,0.5,0.3]}]}` |
| GET | `page?page=1&regions=detected` | PNG with candidate overlays; optional `region=<candidate-id>` isolates one |
| GET | `page?page=1&regions=saved` | PNG with saved-region overlays |
| POST | `process-drawing` | Existing resumable row extraction; saved regions only |

Pages are one-indexed. Boxes are `[left, top, width, height]`, each expressed as a fraction of the visible page. The former placeholder response from `prepare-drawing-crops` has been replaced by real detection; use GET `crops` to read manual crops without running detection.

## Dependencies and limits

Install `requirements.txt` in Python 3.12 and restart the application. It includes compatible pins for `opencv-python-headless==4.12.0.88` and `numpy==2.2.6`. Missing/broken OpenCV returns a clear 503 response from detection; sign-in, existing reviews, and manual crops remain available.

Rendering is grayscale at up to 200 DPI, capped at 12 million pixels and 5,000 pixels on the longest side. Multiple native image masks and connected-component arrays are allocated, so allow several hundred MB per worker for large sheets. Use the provided synchronous Gunicorn workers for PDF processing. A killed worker releases its lease after five minutes; retry resumes saved progress.

Processing stops above 2,500 cells on one page or 200 candidates in a drawing. These bounds protect the detector's pairwise table-assembly operations. No tables are silently truncated. Use manual crops or smaller drawing PDFs when a limit is reached.

This is a ruled-table detector. Borderless tables, weak lines, skew, dense linework, and narrow schedules along the right edge may be missed or misclassified. Large-sheet downscaling can also lose fine lines. Review is required; no accuracy score is fabricated.

## Verification

```bash
python -c "import cv2, numpy; print(cv2.__version__, numpy.__version__)"
python -m unittest discover -s python_tests -v
```

The integration suite tests persistence, failure/resume, empty results, approval revisions, idempotency, manual crop preservation, reset, ownership/CSRF, worker leases, preview rendering, form editing, accepted-only extraction, and PDF rotation/CropBox geometry. Provider replies are mocked.

Four native OpenCV tests cover synthetic separate grids, title-block filtering, scanned/rotated grids, blank/border-only pages, malformed input, and a cell-count bound. They explicitly skip if OpenCV is absent. The included GitHub Actions workflow requires a successful OpenCV import before running the suite so a missing dependency cannot silently pass CI. See VALIDATION.md for what was executed in the build environment.

Before using the detector for project output, test representative native and scanned sheets, rotated/CropBox pages, faint lines, adjacent schedules, title blocks, and oversized sheets. Inspect candidate borders against the original, interrupt/resume detection, adjust one crop, extract its rows with your configured Gemini key, and compare the equipment log with the source.
