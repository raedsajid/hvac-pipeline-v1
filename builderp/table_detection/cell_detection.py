"""
Detect table-cell regions from a drawing sheet's ruling-line grid.

Third stage of the drawing pipeline (after ``line_detection``). It applies the
"inverse fill" idea: instead of detecting whole tables directly, invert the
grid mask so each table *cell* interior becomes an enclosed white blob, label
those blobs with connected components, and discard the page background / noise.
The surviving cell boxes are the raw material the next stage clusters into whole
table bounding boxes.

Ported from the ``drawing_v4`` prototype's ``extract_schedule_tables.py`` (the
connected-components cell step), operating on the in-memory ``GridLines`` from
``line_detection`` instead of files on disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

import cv2
import numpy as np

from .line_detection import GridLines

# All cell filters below are resolution-relative (fractions of the page area or
# of a page dimension), so they hold across render DPIs without retuning.

# A connected blob smaller than this fraction of the page area is treated as
# noise rather than a real table cell (~200 px on the standard render).
_MIN_CELL_AREA_FRAC = 2.5e-6
# The interior page background is rejected by two complementary backstops so
# that a real full-width header row (wide but short) still survives: it has a
# modest area (passes the area test) and a small height (passes the span test).
#   - area: a blob whose filled pixels exceed this fraction of the page is a
#     dense background region.
_BACKGROUND_AREA_FRAC = 0.25
#   - span: a blob whose bounding box exceeds this fraction of BOTH dimensions
#     is a background region that wraps the page even if sparsely filled.
_BACKGROUND_SPAN_FRAC = 0.90
# A blob whose bounding box reaches within this fraction of a sheet dimension of
# any edge is exterior space (the margin/frame around the drawing), not a cell.
_BORDER_TOUCH_FRAC = 0.001


class Box(NamedTuple):
    """Axis-aligned bounding box in pixel space (top-left origin)."""

    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class CellComponents:
    """
    Raw connected-components result for the inverted grid mask.

    Attributes:
        num_labels: number of labels, including label 0 (the rule network).
        labels: ``int32`` label image (same HxW as the sheet); pixel value is
            the component index, 0 = the black rule network.
        stats: ``num_labels`` x 5 array of ``CC_STAT_*`` columns
            (left, top, width, height, area) per component.
        width: source sheet width in pixels.
        height: source sheet height in pixels.
    """

    num_labels: int
    labels: np.ndarray
    stats: np.ndarray
    width: int
    height: int


def find_cell_components(grid: GridLines) -> CellComponents:
    """
    Invert the grid mask and label its enclosed regions as components.

    Inverting turns cell interiors white and the rule network black, so
    ``connectedComponentsWithStats`` labels each enclosed cell as its own
    component (label 0 is the value-0 pixels, i.e. the rule network).

    Inputs: ``GridLines`` from ``detect_grid_lines`` (uses ``grid_mask`` and
        the source ``width``/``height``).
    Outputs: ``CellComponents`` holding the label image and per-component stats.
    """
    # After inverting, cell interiors are white (255) and the rule network is
    # black (0), which becomes connected-component label 0.
    inverted = cv2.bitwise_not(grid.grid_mask)
    num_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        inverted, connectivity=8
    )
    return CellComponents(
        num_labels=num_labels,
        labels=labels,
        stats=stats,
        width=grid.width,
        height=grid.height,
    )


def filter_cells(
    components: CellComponents,
    *,
    min_cell_area_frac: float = _MIN_CELL_AREA_FRAC,
    background_area_frac: float = _BACKGROUND_AREA_FRAC,
    background_span_frac: float = _BACKGROUND_SPAN_FRAC,
    border_touch_frac: float = _BORDER_TOUCH_FRAC,
) -> list[Box]:
    """
    Keep only the components that look like real table cells.

    Drops noise (tiny area), the exterior margin/frame (blobs touching a sheet
    edge), and the interior page background (rejected by two complementary
    backstops: large filled area, or a bounding box spanning most of both sheet
    dimensions). All thresholds are resolution-relative.

    Inputs:
        components: the ``CellComponents`` from ``find_cell_components``.
        min_cell_area_frac: minimum blob area, as a fraction of page area, to
            count as a cell.
        background_area_frac: a blob whose filled area exceeds this fraction of
            the page area is treated as the interior background.
        background_span_frac: a blob whose bounding box exceeds this fraction of
            both sheet dimensions is treated as the interior background.
        border_touch_frac: a blob within this fraction of a sheet dimension of
            any edge is treated as exterior margin/frame.
    Outputs: list of ``Box`` cell boxes in pixel space.
    """
    if components.num_labels <= 1:
        return []

    # Drop label 0 (the rule network) and evaluate the filters on the per-
    # component stat columns as vectors instead of looping in Python.
    stats = components.stats[1:]
    x = stats[:, cv2.CC_STAT_LEFT]
    y = stats[:, cv2.CC_STAT_TOP]
    w = stats[:, cv2.CC_STAT_WIDTH]
    h = stats[:, cv2.CC_STAT_HEIGHT]
    area = stats[:, cv2.CC_STAT_AREA]

    page_area = components.width * components.height
    tol_x = components.width * border_touch_frac
    tol_y = components.height * border_touch_frac

    big_enough = area >= page_area * min_cell_area_frac
    # Exterior space (page margin / frame) reaches the sheet edge; genuine cells
    # sit inside the drawing border.
    touches_border = (
        (x <= tol_x)
        | (y <= tol_y)
        | (x + w >= components.width - tol_x)
        | (y + h >= components.height - tol_y)
    )
    # Interior page background: either dense (large filled area) or a sparse
    # region whose bounding box still wraps most of the page in both axes.
    spans_page = (w >= components.width * background_span_frac) & (
        h >= components.height * background_span_frac
    )
    is_background = (area >= page_area * background_area_frac) | spans_page
    keep = big_enough & ~touches_border & ~is_background

    return [
        Box(int(bx), int(by), int(bw), int(bh))
        for bx, by, bw, bh in zip(x[keep], y[keep], w[keep], h[keep], strict=True)
    ]


def detect_cells(
    grid: GridLines,
    *,
    min_cell_area_frac: float = _MIN_CELL_AREA_FRAC,
    background_area_frac: float = _BACKGROUND_AREA_FRAC,
    background_span_frac: float = _BACKGROUND_SPAN_FRAC,
    border_touch_frac: float = _BORDER_TOUCH_FRAC,
) -> list[Box]:
    """
    Extract candidate table-cell bounding boxes from a grid mask.

    Convenience composition of ``find_cell_components`` and ``filter_cells``.

    Inputs:
        grid: ``GridLines`` from ``detect_grid_lines``.
        min_cell_area_frac: minimum blob area, as a fraction of page area.
        background_area_frac: blobs whose filled area exceeds this fraction of
            page area are treated as the interior background.
        background_span_frac: blobs whose bounding box exceeds this fraction of
            both dimensions are treated as the interior background.
        border_touch_frac: blobs within this fraction of a dimension of an edge
            are treated as exterior margin/frame.
    Outputs: list of ``Box`` cell boxes in pixel space.
    """
    components = find_cell_components(grid)
    boxes = filter_cells(
        components,
        min_cell_area_frac=min_cell_area_frac,
        background_area_frac=background_area_frac,
        background_span_frac=background_span_frac,
        border_touch_frac=border_touch_frac,
    )
    # Drop full-page int32 label map; callers only need the filtered boxes.
    del components
    return boxes
