"""Raster rendering of v6 scenes with skia-python.

M0 strategy (see notes/renderer_plan.md):

- Ink strokes: one antialiased round-capped line segment per point pair,
  stroke width taken from the device-stored nib width at the segment's
  start point (RCU's approach).
- Highlighter: drawn first, globally under all ink, as a single stroked
  polyline per stroke at the stored constant nib width, with a fully
  opaque color -- overlapping highlights merge into a flat union and ink
  stays uncovered on top.
"""

import logging
import typing as tp

import skia

from rmscene import read_tree

from . import pens
from .scene import RenderStroke, extract_strokes, page_size

_logger = logging.getLogger(__name__)


def _paint(rgba: tuple[int, int, int, int]) -> skia.Paint:
    r, g, b, a = rgba
    return skia.Paint(
        AntiAlias=True,
        Color=skia.Color(r, g, b, a),
        Style=skia.Paint.kStroke_Style,
    )


def _draw_ink(canvas: skia.Canvas, stroke: RenderStroke) -> None:
    paint = _paint(stroke.rgba)
    paint.setStrokeCap(skia.Paint.kRound_Cap)
    dx, dy = stroke.offset
    points = stroke.points
    if len(points) == 1:
        p = points[0]
        dot = _paint(stroke.rgba)
        dot.setStyle(skia.Paint.kFill_Style)
        canvas.drawCircle(p.x + dx, p.y + dy, pens.nib_px(stroke.tool, p) / 2, dot)
        return
    for p0, p1 in zip(points, points[1:]):
        paint.setStrokeWidth(pens.nib_px(stroke.tool, p0))
        canvas.drawLine(p0.x + dx, p0.y + dy, p1.x + dx, p1.y + dy, paint)


def _draw_highlight(canvas: skia.Canvas, stroke: RenderStroke) -> None:
    dx, dy = stroke.offset
    points = stroke.points
    paint = _paint(stroke.rgba)
    paint.setStrokeWidth(pens.nib_px(stroke.tool, points[0]))
    paint.setStrokeCap(skia.Paint.kButt_Cap)
    paint.setStrokeJoin(skia.Paint.kRound_Join)
    path = skia.Path()
    path.moveTo(points[0].x + dx, points[0].y + dy)
    for p in points[1:]:
        path.lineTo(p.x + dx, p.y + dy)
    canvas.drawPath(path, paint)


def render_scene(
    canvas: skia.Canvas, strokes: list[RenderStroke], shift_x: float
) -> None:
    canvas.translate(shift_x, 0)
    # Highlights form a global background layer under all ink.
    for stroke in strokes:
        if stroke.highlight:
            _draw_highlight(canvas, stroke)
    for stroke in strokes:
        if not stroke.highlight:
            _draw_ink(canvas, stroke)


def render_png(
    rm_path: str, png_path: str, scale: float = 1.0
) -> tuple[int, int]:
    """Render `rm_path` to a PNG at `png_path`.

    The canvas is the page size from SceneInfo (device screen), times
    `scale`. Returns the output image size.
    """
    with open(rm_path, "rb") as f:
        tree = read_tree(f)
    strokes = extract_strokes(tree)
    page_w, page_h = page_size(tree)
    out_w, out_h = round(page_w * scale), round(page_h * scale)

    surface = skia.Surface(out_w, out_h)
    canvas = surface.getCanvas()
    canvas.clear(skia.ColorWHITE)
    canvas.scale(scale, scale)
    render_scene(canvas, strokes, shift_x=page_w / 2)

    surface.makeImageSnapshot().save(png_path, skia.kPNG)
    _logger.info("Rendered %d strokes to %s (%dx%d)", len(strokes), png_path, out_w, out_h)
    return out_w, out_h
