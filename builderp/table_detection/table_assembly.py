"""
Assemble schedule-table bounding boxes from ruling-line connectivity.

Fourth stage of the drawing pipeline (after ``cell_detection``). Instead of
clustering cells by distance (which welds packed schedules into mega-tables),
run connected components on the undilated horizontal|vertical line union: each
physically connected grid of rules becomes one candidate table box. Size,
cell-density, and internal-grid filters then drop title-block / border-only
notes / stray-ink blobs.

Cells from stage 3 validate line-network candidates (minimum cell count) and
power orphan recovery for short/fragmented single-row schedules. Title rows
above the top rule are left for crop padding downstream.
"""

from __future__ import annotations

import cv2
import numpy as np

from .cell_detection import Box
from .line_detection import GridLines

# All size thresholds are resolution-relative so they hold across render DPIs.

# A table box must be at least this wide/tall/large and no larger than the max
# (which rejects the page frame / background).
_MIN_TABLE_W_FRAC = 0.05
_MIN_TABLE_H_FRAC = 0.01
_MIN_TABLE_AREA_FRAC = 0.001
_MAX_TABLE_AREA_FRAC = 0.85
# A line-network blob counts as a schedule only if at least this many detected
# cells have their center inside it. Rejects title-block rules and stray ink.
_MIN_CELLS_PER_TABLE = 3
# Orphan cells are clustered with a small gap that bridges rule thickness but
# does not jump real inter-table whitespace.
_ORPHAN_GAP_FRAC = 0.005
# Drop a candidate if a smaller candidate's area is this far contained inside it.
# Catches the page-frame line network whose bbox wraps every real table.
_PARENT_CONTAINMENT = 0.85
# Right-edge title-block fragments (Drawn by / Drawing No. / sheet count) sit in
# a narrow column flush to the sheet edge; schedules are wider and farther left.
_TITLE_BLOCK_LEFT_FRAC = 0.85
_TITLE_BLOCK_MAX_W_FRAC = 0.15
# Ignore page-scale blobs when counting cells inside a candidate (their centers
# can land inside a notes box and falsely satisfy the min-cell floor).
_MAX_CELL_AREA_FRAC = 0.05
# Border-only note frames have almost no H/V ink inside the outer border; real
# schedules (and notes attached under the same border as a grid) have both.
_MIN_INTERNAL_H_DENSITY = 0.001
_MIN_INTERNAL_V_DENSITY = 0.001
_INTERNAL_GRID_INSET_FRAC = 0.01
_INTERNAL_GRID_INSET_MIN_PX = 8


def _line_network_boxes(grid: GridLines) -> list[Box]:
    """
    Label connected ruling-line networks and emit one box per network.

    Uses the undilated ``horizontal | vertical`` union so the 3px grid dilate
    (meant for cell sealing) cannot bridge tight whitespace between packed
    tables.

    Inputs: ``GridLines`` from ``detect_grid_lines``.
    Outputs: one ``Box`` per connected line component (label 0 skipped).
    """
    mask = cv2.bitwise_or(grid.horizontal, grid.vertical)
    num_labels, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask, connectivity=8
    )
    boxes: list[Box] = []
    for label in range(1, num_labels):
        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        w = int(stats[label, cv2.CC_STAT_WIDTH])
        h = int(stats[label, cv2.CC_STAT_HEIGHT])
        boxes.append(Box(x, y, w, h))
    return boxes


def _expand_with_cells(box: Box, cells: list[Box]) -> Box:
    """
    Expand a line-network box to include intersecting detected cells.

    Connected-components stats wrap only the line ink itself; for short
    single-row schedules that can make the box slightly too short for the size
    filter. Expanding to intersecting cells restores the full table body.

    Inputs: a line-network box and the detected stage-3 cell boxes.
    Outputs: the union of the input box and all intersecting cell boxes.
    """
    x1, y1 = box.x, box.y
    x2, y2 = box.x + box.w, box.y + box.h
    base_x1, base_y1 = x1, y1
    base_x2, base_y2 = x2, y2
    for cell in cells:
        cell_x2 = cell.x + cell.w
        cell_y2 = cell.y + cell.h
        intersects = not (
            cell_x2 <= base_x1
            or cell.x >= base_x2
            or cell_y2 <= base_y1
            or cell.y >= base_y2
        )
        if intersects:
            x1 = min(x1, cell.x)
            y1 = min(y1, cell.y)
            x2 = max(x2, cell_x2)
            y2 = max(y2, cell_y2)
    return Box(x1, y1, x2 - x1, y2 - y1)


def _is_plausible_table_size(box: Box, width: int, height: int) -> bool:
    """
    Check whether one candidate box passes the standard table size floor/ceiling.

    Inputs: a candidate box and the source width/height in pixels.
    Outputs: ``True`` when the box meets the standard width/height/area bounds.
    """
    page_area = width * height
    min_w = width * _MIN_TABLE_W_FRAC
    min_h = height * _MIN_TABLE_H_FRAC
    min_area = page_area * _MIN_TABLE_AREA_FRAC
    max_area = page_area * _MAX_TABLE_AREA_FRAC
    area = box.w * box.h
    return min_area <= area <= max_area and box.w >= min_w and box.h >= min_h


def _rescue_undersized_candidates(
    boxes: list[Box],
    cells: list[Box],
    *,
    width: int,
    height: int,
) -> list[Box]:
    """
    Expand only undersized line-network candidates with intersecting cells.

    Most connected-line boxes are already correct and should stay untouched.
    This rescue path targets short single-row schedules whose ink-only CC box
    fails the standard size floor but whose intersecting cells reveal the full
    table body.

    Inputs: line-network boxes, detected stage-3 cells, and the source size.
    Outputs: original plausible boxes plus rescued expansions that now pass.
    """
    rescued: list[Box] = []
    for box in boxes:
        if _is_plausible_table_size(box, width, height):
            rescued.append(box)
            continue
        expanded = _expand_with_cells(box, cells)
        if _is_plausible_table_size(expanded, width, height):
            rescued.append(expanded)
    return rescued


def _filter_orphan_clusters(boxes: list[Box], width: int, height: int) -> list[Box]:
    """
    Keep only plausible orphan-table clusters, relaxing the height floor.

    Orphan recovery is specifically for single-row schedules whose assembled
    line box was too short or fragmented, so a strict minimum height would
    throw them away again.

    Inputs: candidate orphan boxes and the source width/height in pixels.
    Outputs: size-filtered orphan table boxes.
    """
    page_area = width * height
    min_w = width * _MIN_TABLE_W_FRAC
    min_area = page_area * _MIN_TABLE_AREA_FRAC
    max_area = page_area * _MAX_TABLE_AREA_FRAC
    kept: list[Box] = []
    for box in boxes:
        area = box.w * box.h
        if area < min_area or area > max_area:
            continue
        if box.w < min_w:
            continue
        kept.append(box)
    return kept


def _filter_by_cell_density(
    boxes: list[Box],
    cells: list[Box],
    *,
    page_area: int,
    min_cells: int = _MIN_CELLS_PER_TABLE,
    max_cell_area_frac: float = _MAX_CELL_AREA_FRAC,
) -> list[Box]:
    """
    Keep only boxes that enclose enough detected cells.

    A connected line blob without a real cell grid (title block, isolated
    rules) fails this check. Cell membership uses the cell center. Page-scale
    blobs are ignored so their centers cannot pad a border-only notes box up
    to the minimum count.

    Inputs: candidate table boxes, stage-3 cell boxes, page area in pixels,
        the minimum count, and the max cell-area fraction of the page to count.
    Outputs: boxes that contain at least ``min_cells`` usable cell centers.
    """
    usable = [
        cell for cell in cells if cell.w * cell.h <= page_area * max_cell_area_frac
    ]
    if not usable:
        return []

    centers = np.array(
        [(c.x + c.w // 2, c.y + c.h // 2) for c in usable], dtype=np.int32
    )
    kept: list[Box] = []
    for box in boxes:
        inside = (
            (centers[:, 0] >= box.x)
            & (centers[:, 0] < box.x + box.w)
            & (centers[:, 1] >= box.y)
            & (centers[:, 1] < box.y + box.h)
        )
        if int(inside.sum()) >= min_cells:
            kept.append(box)
    return kept


def _has_internal_grid(
    box: Box,
    grid: GridLines,
    *,
    min_h_density: float = _MIN_INTERNAL_H_DENSITY,
    min_v_density: float = _MIN_INTERNAL_V_DENSITY,
    inset_frac: float = _INTERNAL_GRID_INSET_FRAC,
    inset_min_px: int = _INTERNAL_GRID_INSET_MIN_PX,
) -> bool:
    """
    Return whether a box has meaningful internal ruling structure.

    Insets past the outer border so a notes frame with no internal grid fails,
    while a schedule (or notes sharing a border with a grid) still passes.
    Short single-row schedules often keep H rules only on the outer border
    (removed by the inset); those still pass when vertical column rules remain.

    Inputs: candidate box, ``GridLines`` masks, density floors, and inset
        controls.
    Outputs: ``True`` when the box looks like a ruled table rather than a
        border-only notes frame.
    """
    inset = max(int(min(box.w, box.h) * inset_frac), inset_min_px)
    inset = min(inset, max(box.h // 4, 1), max(box.w // 4, 1))
    x1 = box.x + inset
    y1 = box.y + inset
    x2 = box.x + box.w - inset
    y2 = box.y + box.h - inset
    if x2 <= x1 or y2 <= y1:
        return False

    area = (x2 - x1) * (y2 - y1)
    h_den = int(cv2.countNonZero(grid.horizontal[y1:y2, x1:x2])) / area
    v_den = int(cv2.countNonZero(grid.vertical[y1:y2, x1:x2])) / area
    if h_den >= min_h_density and v_den >= min_v_density:
        return True
    # Single-row scraps: column rules remain after inset; H rules may not.
    short_row = box.h <= max(4 * inset, int(grid.height * 0.02))
    return short_row and v_den >= min_v_density


def _filter_by_internal_grid(boxes: list[Box], grid: GridLines) -> list[Box]:
    """
    Keep only boxes that contain an internal H+V ruling grid.

    Inputs: candidate table boxes and ``GridLines`` from line detection.
    Outputs: boxes that pass ``_has_internal_grid``.
    """
    return [box for box in boxes if _has_internal_grid(box, grid)]


def _merge_by_proximity(cells: list[Box], gap_x: int, gap_y: int) -> list[Box]:
    """
    Union orphan cell boxes whose gap-inflated rectangles touch.

    This is used only as a recovery path for cells not already covered by an
    accepted line-network table, so the small gap bridges rule thickness but
    does not bring back the old full-sheet mega-merges.

    Inputs: orphan cell boxes and the horizontal/vertical merge gaps in pixels.
    Outputs: one ``Box`` per recovered cluster.
    """
    boxes = [[c.x, c.y, c.x + c.w, c.y + c.h] for c in cells]
    changed = True
    while changed:
        changed = False
        merged: list[list[int]] = []
        used = [False] * len(boxes)
        for i in range(len(boxes)):
            if used[i]:
                continue
            current = list(boxes[i])
            used[i] = True
            for j in range(i + 1, len(boxes)):
                if used[j]:
                    continue
                other = boxes[j]
                if (
                    current[0] - gap_x <= other[2]
                    and current[2] + gap_x >= other[0]
                    and current[1] - gap_y <= other[3]
                    and current[3] + gap_y >= other[1]
                ):
                    current[0] = min(current[0], other[0])
                    current[1] = min(current[1], other[1])
                    current[2] = max(current[2], other[2])
                    current[3] = max(current[3], other[3])
                    used[j] = True
                    changed = True
            merged.append(current)
        boxes = merged
    return [Box(b[0], b[1], b[2] - b[0], b[3] - b[1]) for b in boxes]


def _drop_containing_parents(
    boxes: list[Box],
    *,
    containment: float = _PARENT_CONTAINMENT,
) -> list[Box]:
    """
    Drop boxes that wrap a smaller candidate (page-frame parents).

    The drawing border often forms one large connected line network whose
    bounding box contains every schedule. Cell-density cannot reject it
    (every cell center falls inside), so drop any box that largely contains
    another surviving candidate.

    Inputs: candidate boxes and the containment threshold (fraction of the
        smaller box's area that must lie inside the larger).
    Outputs: boxes with containing parents removed.
    """
    if len(boxes) < 2:
        return boxes

    drop: set[int] = set()
    for i, parent in enumerate(boxes):
        parent_area = parent.w * parent.h
        for j, child in enumerate(boxes):
            if i == j or j in drop:
                continue
            child_area = child.w * child.h
            if child_area >= parent_area:
                continue
            x_a = max(parent.x, child.x)
            y_a = max(parent.y, child.y)
            x_b = min(parent.x + parent.w, child.x + child.w)
            y_b = min(parent.y + parent.h, child.y + child.h)
            inter = max(0, x_b - x_a) * max(0, y_b - y_a)
            if inter > 0 and inter / child_area > containment:
                drop.add(i)
                break
    return [box for index, box in enumerate(boxes) if index not in drop]


def _recover_orphan_tables(
    cells: list[Box],
    tables: list[Box],
    *,
    width: int,
    height: int,
) -> list[Box]:
    """
    Recover table boxes from uncovered cells using a small-gap cell cluster.

    Single-row schedules can survive cell detection while failing the line-CC
    path because the line network is fragmented or too short. This fallback
    only considers cells not already claimed by a table box. Page-scale blobs
    are excluded so they cannot weld every orphan into one mega-box.

    Inputs: detected cells, accepted table boxes, and the source dimensions.
    Outputs: additional recovered table boxes not already covered by ``tables``.
    """
    if not cells:
        return []

    page_area = width * height
    max_cell_area = page_area * _MAX_CELL_AREA_FRAC
    orphan_cells: list[Box] = []
    for cell in cells:
        if cell.w * cell.h > max_cell_area:
            continue
        cx = cell.x + cell.w // 2
        cy = cell.y + cell.h // 2
        covered = any(
            table.x <= cx < table.x + table.w and table.y <= cy < table.y + table.h
            for table in tables
        )
        if not covered:
            orphan_cells.append(cell)

    if not orphan_cells:
        return []

    gap_x = max(int(width * _ORPHAN_GAP_FRAC), 1)
    gap_y = max(int(height * _ORPHAN_GAP_FRAC), 1)
    clusters = _merge_by_proximity(orphan_cells, gap_x, gap_y)
    sized = _filter_orphan_clusters(clusters, width, height)
    dense = _filter_by_cell_density(sized, orphan_cells, page_area=page_area)

    recovered: list[Box] = []
    for box in dense:
        duplicate = False
        box_area = box.w * box.h
        for table in tables:
            x_a = max(box.x, table.x)
            y_a = max(box.y, table.y)
            x_b = min(box.x + box.w, table.x + table.w)
            y_b = min(box.y + box.h, table.y + table.h)
            inter = max(0, x_b - x_a) * max(0, y_b - y_a)
            if inter > 0 and inter / box_area > _PARENT_CONTAINMENT:
                duplicate = True
                break
        if not duplicate:
            recovered.append(box)
    return recovered


def filter_schedule_tables(
    tables: list[Box],
    *,
    width: int,
    title_block_left_frac: float = _TITLE_BLOCK_LEFT_FRAC,
    title_block_max_w_frac: float = _TITLE_BLOCK_MAX_W_FRAC,
) -> list[Box]:
    """
    Drop right-edge title-block boxes that are not equipment schedules.

    Title-block fragments are narrow and start in the right margin of the sheet.
    Real schedules are wider and sit farther left, so both conditions are
    required before a box is rejected.

    Inputs:
        tables: candidate table boxes from ``assemble_tables``.
        width: source sheet width in pixels.
        title_block_left_frac: minimum left-edge fraction of sheet width for a
            box to count as title-block-anchored.
        title_block_max_w_frac: maximum width fraction of sheet width for a
            title-block column.
    Outputs: schedule table boxes with title-block fragments removed.
    """
    min_left = width * title_block_left_frac
    max_w = width * title_block_max_w_frac
    return [box for box in tables if not (box.x >= min_left and box.w <= max_w)]


def assemble_tables(cells: list[Box], grid: GridLines) -> list[Box]:
    """
    Group ruling-line networks into one bounding box per schedule table.

    Pipeline: connected components on undilated H|V lines -> rescue undersized
    candidates with intersecting cells -> cell-density filter -> drop
    containing parents -> recover uncovered cell clusters -> require an
    internal H+V grid (rejects border-only note frames).

    Inputs:
        cells: per-cell boxes from ``detect_cells`` (validation and orphan
            recovery).
        grid: ``GridLines`` from ``detect_grid_lines`` (uses undilated
            horizontal/vertical masks and source width/height).
    Outputs: table bounding boxes, sorted top-to-bottom then left-to-right.
    """
    if not cells:
        return []

    page_area = grid.width * grid.height
    candidates = _line_network_boxes(grid)
    sized = _rescue_undersized_candidates(
        candidates,
        cells,
        width=grid.width,
        height=grid.height,
    )
    dense = _filter_by_cell_density(sized, cells, page_area=page_area)
    tables = _drop_containing_parents(dense)
    recovered = _recover_orphan_tables(
        cells,
        tables,
        width=grid.width,
        height=grid.height,
    )
    combined = _drop_containing_parents(tables + recovered)
    combined = _filter_by_internal_grid(combined, grid)
    return sorted(combined, key=lambda b: (b.y, b.x))
