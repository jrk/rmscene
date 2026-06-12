"""Pen models: resolve color and per-point nib width for each tool.

Geometry comes from the per-point `width` stored by the device
(quarter-pixels; the device's own brush dynamics output). Per-pen width
multipliers and behaviors follow RCU/remy/rmrl -- see
notes/renderer_research.md for sources. Tools we have not calibrated yet
use the generic stored width.
"""

import logging

from rmscene.scene_items import Pen, PenColor, Point

_logger = logging.getLogger(__name__)

# Palette for indexed colors (same facts as rmc/remt; device >= 3.x stores
# explicit color_rgba for highlight colors).
RM_PALETTE: dict[PenColor, tuple[int, int, int, int]] = {
    PenColor.BLACK: (0, 0, 0, 255),
    PenColor.GRAY: (144, 144, 144, 255),
    PenColor.WHITE: (255, 255, 255, 255),
    PenColor.YELLOW: (251, 247, 25, 255),
    PenColor.GREEN: (0, 255, 0, 255),
    PenColor.PINK: (255, 192, 203, 255),
    PenColor.BLUE: (78, 105, 201, 255),
    PenColor.RED: (179, 62, 57, 255),
    PenColor.GRAY_OVERLAP: (125, 125, 125, 255),
    # Legacy highlight without explicit color_rgba: pastel yellow guess,
    # to be calibrated against a device render.
    PenColor.HIGHLIGHT: (255, 237, 117, 255),
    PenColor.GREEN_2: (161, 216, 125, 255),
    PenColor.CYAN: (139, 208, 229, 255),
    PenColor.MAGENTA: (183, 130, 205, 255),
    PenColor.YELLOW_2: (247, 232, 81, 255),
}

WHITE = (255, 255, 255, 255)


def resolve_color(
    tool: Pen,
    color: PenColor,
    color_rgba: tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int]:
    if tool == Pen.ERASER:
        return WHITE
    if color_rgba is not None:
        # Highlight colors are absolute since device 2.11: ignore stored alpha.
        return (*color_rgba[:3], 255)
    if color in RM_PALETTE:
        return RM_PALETTE[color]
    _logger.warning("Unknown color %s; falling back to black", color)
    return RM_PALETTE[PenColor.BLACK]


# Width multipliers applied to the stored nib width (point.width / 4).
# Sources: RCU pens (pencil 0.58, mech 1/1.5, marker 0.7, paintbrush 0.75).
_NIB_SCALE: dict[Pen, float] = {
    Pen.PENCIL_1: 0.58,
    Pen.PENCIL_2: 0.58,
    Pen.MECHANICAL_PENCIL_1: 1 / 1.5,
    Pen.MECHANICAL_PENCIL_2: 1 / 1.5,
    Pen.MARKER_1: 0.7,
    Pen.MARKER_2: 0.7,
    Pen.PAINTBRUSH_1: 0.75,
    Pen.PAINTBRUSH_2: 0.75,
}


def nib_px(tool: Pen, point: Point) -> float:
    """Final nib width in page pixels at this point."""
    return (point.width / 4) * _NIB_SCALE.get(tool, 1.0)


def is_highlight(tool: Pen) -> bool:
    return Pen.is_highlighter(tool)


def should_skip(tool: Pen) -> bool:
    # The device ignores erase-area strokes.
    return tool == Pen.ERASER_AREA
