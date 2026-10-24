"""
Detect table ruling-line structure in a rendered drawing sheet.

Second stage of the drawing pipeline (after ``pdf_to_image``): given the
full-page sheet as PNG bytes, isolate the horizontal and vertical ruling lines
that make up equipment-schedule tables using OpenCV morphology, then union
them into a single grid mask. Downstream stages turn this grid into per-cell
regions, cluster them into table bounding boxes, and crop each table.

Ported from the ``drawing_v4`` prototype's "inverse fill" table detector, the
first step only (decode -> binarize -> per-axis line detection -> grid mask),
adapted to operate on in-memory image bytes instead of files on disk.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# NOTE: the absolute-pixel constants below (block size, min kernel, dilate
# size) assume a stable render DPI (``settings.drawing_render_dpi``). The line
# kernel *lengths* scale with the image dimensions and so track DPI on their
# own, but these absolute values do not; revisit them if the render DPI changes.

# Adaptive-threshold neighborhood (odd px) and mean offset. Large enough to
# span a ruling line's local background yet small enough to follow uneven sheet
# lighting; global thresholding would drop faint rules. Prototype-tuned values.
_ADAPTIVE_BLOCK_SIZE = 25
_ADAPTIVE_C = 12
# A dark run at least (image dimension / divisor) px long, floored at
# ``_MIN_LINE_KERNEL``, counts as a ruling line. Sizing the kernel from the
# image dimension keeps line detection roughly proportional across render DPIs.
# Tuned aggressively for recall: a large divisor -> short kernels -> a short
# minimum detectable run, recovering short/faintly-fragmented rules at the cost
# of admitting more non-line ink. The floor is kept below the shortest computed
# kernel (vertical is height/160 = 45 px on the standard render) so it does not
# clamp the divisor.
_LINE_KERNEL_DIVISOR = 160
_MIN_LINE_KERNEL = 40
# A single open (erode then dilate) sets the minimum detectable run to ~1x the
# kernel length -- the most permissive an open can be. A second iteration would
# roughly double the threshold (better noise suppression, worse recall); neither
# bridges gaps (that would be a closing). Kept at 1 to favor recall.
_MORPH_ITERATIONS = 1
# Thicken the combined grid a few px so each cell interior is fully enclosed
# before the (later) connected-components step.
_GRID_DILATE_KERNEL = 3


@dataclass(frozen=True)
class GridLines:
    """
    Ruling-line masks extracted from a rendered drawing sheet.

    All masks are 2-D ``uint8`` arrays where 255 marks foreground (ink/line)
    and 0 marks background.

    Attributes:
        horizontal: isolated horizontal ruling lines.
        vertical: isolated vertical ruling lines.
        grid_mask: dilated union of the horizontal and vertical lines.
        width: source image width in pixels.
        height: source image height in pixels.
    """

    horizontal: np.ndarray
    vertical: np.ndarray
    grid_mask: np.ndarray
    width: int
    height: int


def _decode_grayscale(image_png: bytes) -> np.ndarray:
    """
    Decode PNG bytes into a single-channel grayscale image.

    Inputs: PNG-encoded image bytes (e.g. from ``render_pdf_to_image``).
    Outputs: 2-D ``uint8`` grayscale array.
    Raises: ``ValueError`` if the bytes cannot be decoded as an image.
    """
    if not image_png:
        raise ValueError("Image bytes must not be empty.")
    buffer = np.frombuffer(image_png, dtype=np.uint8)
    # cv2.imdecode returns None (rather than raising) on unreadable input.
    gray = cv2.imdecode(buffer, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError("Could not decode the provided bytes as an image.")
    return gray


def binarize_sheet(
    image_png: bytes,
    *,
    block_size: int = _ADAPTIVE_BLOCK_SIZE,
    c: int = _ADAPTIVE_C,
) -> np.ndarray:
    """
    Decode a sheet and adaptively threshold it into an inverted binary mask.

    Adaptive (local-mean) thresholding follows uneven sheet lighting that a
    global threshold would miss; ``THRESH_BINARY_INV`` makes ink white (255) on
    a black (0) background -- the polarity the line-detection morphology
    expects. This is the pre-morphology stage, exposed so callers/visualizers
    can inspect exactly what survived thresholding before any lines are opened.

    Inputs:
        image_png: full-page drawing sheet as PNG bytes.
        block_size: adaptive-threshold neighborhood size in px (odd, >= 3).
        c: constant subtracted from the local mean; higher keeps only stronger
            ink.
    Outputs: 2-D ``uint8`` mask (255 = ink, 0 = background).
    Raises: ``ValueError`` if ``block_size`` is not an odd integer >= 3, or if
        the image cannot be decoded.
    """
    if block_size < 3 or block_size % 2 == 0:
        raise ValueError(f"block_size must be an odd integer >= 3, got {block_size}.")
    gray = _decode_grayscale(image_png)
    return cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        blockSize=block_size,
        C=c,
    )


def _detect_axis_lines(binary: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """
    Isolate lines along one axis via a morphological open.

    A morphological open (erode then dilate) with a long, 1-px-thick kernel
    keeps only runs that fit the kernel's orientation and length, erasing text
    and non-line ink.

    Inputs: inverted binary image and a 1-D structuring element (wide for
        horizontal rules, tall for vertical rules).
    Outputs: ``uint8`` mask containing only the matching lines.
    """
    return cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, kernel, iterations=_MORPH_ITERATIONS
    )


def detect_grid_lines(
    image_png: bytes,
    *,
    block_size: int = _ADAPTIVE_BLOCK_SIZE,
    c: int = _ADAPTIVE_C,
    line_kernel_divisor: int = _LINE_KERNEL_DIVISOR,
) -> GridLines:
    """
    Extract table ruling-line masks from a rendered drawing sheet.

    Pipeline: decode -> adaptive threshold (inverted, so ink becomes white on
    black) -> morphological open with a wide kernel for horizontal rules and a
    tall kernel for vertical rules -> union the two and dilate into one grid
    mask.

    Inputs:
        image_png: full-page drawing sheet as PNG bytes (from
            ``render_pdf_to_image``).
        block_size: adaptive-threshold neighborhood size in pixels (must be an
            odd number >= 3).
        c: constant subtracted from the local mean in adaptive thresholding;
            higher values keep only stronger ink.
        line_kernel_divisor: the image width/height is divided by this to size
            the horizontal/vertical line kernels (larger divisor -> shorter
            minimum line length detected).
    Outputs: ``GridLines`` holding the per-axis and grid masks plus the source
        dimensions.
    Raises: ``ValueError`` if ``block_size`` is not an odd integer >= 3, or if
        the image cannot be decoded.
    """
    if not isinstance(line_kernel_divisor, int) or line_kernel_divisor < 1:
        raise ValueError("line_kernel_divisor must be a positive integer.")
    binary = binarize_sheet(image_png, block_size=block_size, c=c)
    height, width = binary.shape[:2]

    horiz_len = max(width // line_kernel_divisor, _MIN_LINE_KERNEL)
    horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (horiz_len, 1))
    horizontal = _detect_axis_lines(binary, horiz_kernel)

    vert_len = max(height // line_kernel_divisor, _MIN_LINE_KERNEL)
    vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, vert_len))
    vertical = _detect_axis_lines(binary, vert_kernel)
    # Binary is unused after axis opens; drop before allocating grid_mask.
    del binary

    grid_mask = cv2.bitwise_or(horizontal, vertical)
    thicken = cv2.getStructuringElement(
        cv2.MORPH_RECT, (_GRID_DILATE_KERNEL, _GRID_DILATE_KERNEL)
    )
    grid_mask = cv2.dilate(grid_mask, thicken, iterations=1)

    return GridLines(
        horizontal=horizontal,
        vertical=vertical,
        grid_mask=grid_mask,
        width=width,
        height=height,
    )
