"""
Stages 2-4: ruling-line detection, cell detection, and table assembly.
"""

from .cell_detection import Box, detect_cells
from .line_detection import (
    GridLines,
    detect_grid_lines,
)
from .table_assembly import (
    assemble_tables,
    filter_schedule_tables,
)

__all__ = [
    "Box",
    "GridLines",
    "assemble_tables",
    "detect_cells",
    "detect_grid_lines",
    "filter_schedule_tables",
]
