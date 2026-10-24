from .store import write
"""Resumable, in-process table detection and explicit crop approval."""
import hashlib
import json
import math

import fitz

from . import drawings, pdf
from .store import (AppError, event, get_bytes, get_json, job_lock, now, one,
                    put_json, run, transaction)

VERSION = 'ruling-lines-v1'
MAX_PIXELS = 12_000_000
MAX_SIDE = 5000
MAX_CELLS = 2500
MAX_TABLES = 200


def detection_key(did):
    return f'python/table-detection/{did}.json'


def state(did, uid):
    d = drawings.drawing(did, uid)
    result = get_json(detection_key(did), {
        'version': VERSION, 'revision': 0, 'cursor': 0, 'pages': d['pages'],
        'proposals': [], 'page_results': [],
    })
    job = one("SELECT error,lease FROM python_jobs WHERE doc_id=? AND kind='detect-tables'", did)
    result['done'] = result['cursor'] >= d['pages']
    result['progress'] = round(result['cursor'] / d['pages'] * 100)
    result['error'] = job['error'] if job else None
    result['busy'] = bool(job and job['lease'] > now())
    return result


def load_detector():
    # Delay native imports so missing OpenCV does not prevent sign-in/manual review.
    try:
        from . import table_detection
        return table_detection
    except (ImportError, OSError) as exc:
        raise AppError(503, 'OpenCV could not load. Install requirements.txt in the Python environment running BuildERP, then restart.', 'detector_unavailable') from exc


def render_for_detection(data, page_number):
    """Rasterize the visible (rotated/cropped) page with bounded allocation."""
    with pdf.open_pdf(data) as document:
        if not 1 <= page_number <= len(document):
            raise AppError(400, 'Invalid drawing page.')
        page = document[page_number - 1]
        width, height = page.rect.width, page.rect.height
        # Leave a rounding margin because MuPDF rounds pixel dimensions upward.
        scale = min(200 / 72, (MAX_SIDE - 2) / max(width, height),
                    math.sqrt((MAX_PIXELS - 2 * MAX_SIDE) / (width * height)))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False)
        if pix.width * pix.height > MAX_PIXELS or max(pix.width, pix.height) > MAX_SIDE:
            raise AppError(413, 'This sheet is too large to render safely. Split it into smaller sheets.')
        return pix.tobytes('png'), {
            'width': pix.width, 'height': pix.height, 'dpi': round(scale * 72, 1),
            'page_width': width, 'page_height': height,
        }


def normalize_box(box, geometry, padding=True):
    """Pixel coordinates -> visible-page fractions used by review and PDF crops."""
    x, y, w, h = map(float, box)
    width, height = geometry['width'], geometry['height']
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        raise AppError(502, 'The detector returned an invalid region.')
    px = 4 / geometry['page_width'] if padding else 0
    top = 18 / geometry['page_height'] if padding else 0
    bottom = 4 / geometry['page_height'] if padding else 0
    x0, y0 = max(0, x / width - px), max(0, y / height - top)
    x1, y1 = min(1, (x + w) / width + px), min(1, (y + h) / height + bottom)
    if x1 <= x0 or y1 <= y0:
        raise AppError(502, 'The detected region falls outside the sheet.')
    return [x0, y0, x1 - x0, y1 - y0]


def detect_page(data, page_number):
    detector = load_detector()
    image, geometry = render_for_detection(data, page_number)
    grid = detector.detect_grid_lines(image)
    cells = detector.detect_cells(grid)
    if len(cells) > MAX_CELLS:
        raise AppError(422, 'This page has too many grid cells for automatic detection. Use manual crops for this drawing.')
    tables = detector.filter_schedule_tables(detector.assemble_tables(cells, grid), width=grid.width)
    if len(tables) > MAX_TABLES:
        raise AppError(422, 'Too many possible tables. Use manual crops or split the drawing.')
    proposals = []
    for box in tables:
        raw = normalize_box(box, geometry, padding=False)
        identity = json.dumps([page_number, [round(v, 7) for v in raw]])
        proposals.append({
            'id': 'detected-' + hashlib.sha256(identity.encode()).hexdigest()[:24],
            'page': page_number, 'box': normalize_box(box, geometry), 'raw_box': raw,
        })
    return proposals, dict(geometry, page=page_number, cells=len(cells), tables=len(proposals))


def detect_step(did, uid):
    d = drawings.drawing(did, uid)
    with job_lock(did, 'detect-tables') as job:
        current = state(did, uid)
        if current['done']:
            return current
        page = current['cursor'] + 1
        proposals, info = detect_page(get_bytes('pdf/' + d['hash']), page)
        if len(current['proposals']) + len(proposals) > MAX_TABLES:
            raise AppError(422, 'This drawing exceeds 200 candidate tables. Split it into smaller PDFs or use manual crops.')
        with transaction():
            drawings.drawing(did, uid)
            active = one("SELECT lease FROM python_jobs WHERE doc_id=? AND kind='detect-tables'", did)
            if not active or active['lease'] != job['lease'] or active['lease'] <= now():
                raise AppError(409, 'Detection was interrupted. Retry to resume saved pages.')
            if state(did, uid)['revision'] != current['revision']:
                raise AppError(409, 'Detection changed. Reload and retry.')
            result = dict(version=VERSION, revision=current['revision'] + 1,
                          cursor=page, pages=d['pages'], proposals=current['proposals'] + proposals,
                          page_results=current['page_results'] + [info])
            put_json(detection_key(did), result)
            write('UPDATE python_jobs SET cursor=?,total=?,status=? WHERE doc_id=? AND kind=?',
                page, d['pages'], 'ready' if page == d['pages'] else 'processing', did, 'detect-tables')
            if page == d['pages']:
                event(d['project_id'], f"Detected {len(result['proposals'])} table candidates for review")
    return state(did, uid)


def reset(did, uid, body):
    """Discard only suggestions; existing reviewed crops and extracted rows survive."""
    with transaction():
        current = state(did, uid)
        if current['busy']:
            raise AppError(409, 'Wait for the current detection page to finish.')
        if body.get('revision') != current['revision']:
            raise AppError(409, 'Detection changed. Reload before restarting.')
        put_json(detection_key(did), dict(version=VERSION, revision=current['revision'] + 1,
                 cursor=0, pages=current['pages'], proposals=[], page_results=[]))
        write("DELETE FROM python_jobs WHERE doc_id=? AND kind='detect-tables'", did)
    return state(did, uid)


def accept(did, uid, body):
    ids = body.get('ids')
    if not isinstance(ids, list) or not ids or len(ids) > MAX_TABLES or any(not isinstance(i, str) for i in ids):
        raise AppError(400, 'Select at least one detected table to accept.')
    with transaction():
        current = state(did, uid)
        if body.get('detection_revision') != current['revision']:
            raise AppError(409, 'Detection changed. Reload before accepting tables.')
        saved = drawings.crops(did, uid)
        if body.get('revision') != saved['revision']:
            raise AppError(409, 'Crops changed. Reload before accepting tables.')
        known = {p['id']: p for p in current['proposals']}
        if any(i not in known for i in ids):
            raise AppError(400, 'One of these detected tables is no longer available.')
        existing = {c['id'] for c in saved['crops']}
        added = [dict(id=p['id'], page=p['page'], box=p['box'])
                 for p in current['proposals'] if p['id'] in ids and p['id'] not in existing]
        # Repeated approval must not erase extracted rows or edited crop coordinates.
        if not added:
            return saved
        d = drawings.drawing(did, uid)
        validated = drawings.validate_crops(d, saved['crops'] + added)
        return drawings.save_validated(d, uid, validated, saved['revision'])
