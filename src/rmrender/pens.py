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
# Starting points from RCU pens, then calibrated against official
# renders via rmrender.calibrate.
NIB_SCALE: dict[Pen, float] = {
    # Solid pens render ~7% narrower than the stored nib on-device
    # (calibrated: ink pixel counts and IoU peak at 0.92).
    Pen.BALLPOINT_1: 0.92,
    Pen.BALLPOINT_2: 0.92,
    Pen.FINELINER_1: 0.92,
    Pen.FINELINER_2: 0.92,
    Pen.CALIGRAPHY: 0.92,
    Pen.PENCIL_1: 0.58,
    Pen.PENCIL_2: 0.58,
    Pen.MECHANICAL_PENCIL_1: 0.9,
    Pen.MECHANICAL_PENCIL_2: 0.9,
    Pen.MARKER_1: 0.7,
    Pen.MARKER_2: 0.7,
    Pen.PAINTBRUSH_1: 0.75,
    Pen.PAINTBRUSH_2: 0.75,
}
_NIB_SCALE = NIB_SCALE

_PAINTBRUSH = (Pen.PAINTBRUSH_1, Pen.PAINTBRUSH_2)
_PENCIL = (Pen.PENCIL_1, Pen.PENCIL_2)
_MECH_PENCIL = (Pen.MECHANICAL_PENCIL_1, Pen.MECHANICAL_PENCIL_2)

# Shader: translucent, accumulates across strokes (unlike highlighter).
# Single-coverage gray measured at ~187/255 in official renders.
SHADER_ALPHA = 0.235


def nib_px(tool: Pen, point: Point) -> float:
    """Final nib width in page pixels at this point."""
    nib = (point.width / 4) * _NIB_SCALE.get(tool, 1.0)
    if tool in _PAINTBRUSH:
        # RCU: pressure narrows the brush below the stored nib (clamped
        # so saturated pressure never widens it).
        nib *= 1 + 0.75 * (min(point.pressure * 0.005, 1.0) - 1)
    return nib


# Stipple density curves: coverage = base + scale * p**gamma, clamped to 1.
# Calibrated against official renders via rmrender.calibrate (mae_blur).
PENCIL_CURVE = (0.05, 0.62, 1.6)
MECH_CURVE = (0.45, 0.6, 1.0)

# Pencil "spatter": a wider, sparser stamp behind the primary one,
# giving the fuzzy edge spread of the real pencil (RCU draws an
# analogous second pass). (width multiplier, coverage multiplier)
PENCIL_SPATTER = (1.5, 0.2)


def spatter(tool: Pen) -> tuple[float, float] | None:
    if tool in _PENCIL:
        return PENCIL_SPATTER
    return None


def is_stippled(tool: Pen) -> bool:
    """Pens the device renders as pure black grain at varying density."""
    return tool in _PENCIL or tool in _MECH_PENCIL


def stipple_coverage(tool: Pen, pressure: float) -> float:
    """Grain density (0..1) at `pressure` (raw 0-255)."""
    p = pressure / 255
    base, scale, gamma = PENCIL_CURVE if tool in _PENCIL else MECH_CURVE
    return min(1.0, base + scale * p**gamma)


_BALLPOINT = (Pen.BALLPOINT_1, Pen.BALLPOINT_2)

# Ballpoint "railroading": at light pressure the device leaves a lighter
# streak along the stroke core (ink riding the nib rims), visible at
# stroke starts/ends and fast inter-letter links. Hole strength ramps in
# below this normalized pressure.
RAILROAD_PRESSURE = 0.3


def railroad_hole(tool: Pen, pressure: float) -> float:
    """0 = solid stroke; 1 = fully starved core (ballpoint only)."""
    if tool not in _BALLPOINT:
        return 0.0
    p = pressure / 255
    return max(0.0, (RAILROAD_PRESSURE - p) / RAILROAD_PRESSURE)


def is_highlight(tool: Pen) -> bool:
    return Pen.is_highlighter(tool)


def is_shader(tool: Pen) -> bool:
    return tool == Pen.SHADER


def should_skip(tool: Pen) -> bool:
    # The device ignores erase-area strokes.
    return tool == Pen.ERASER_AREA
