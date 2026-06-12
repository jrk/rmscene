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
import math
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
        # Average the endpoints' nibs: closer to the device's smooth
        # width interpolation than using either endpoint alone.
        paint.setStrokeWidth(
            (pens.nib_px(stroke.tool, p0) + pens.nib_px(stroke.tool, p1)) / 2
        )
        canvas.drawLine(p0.x + dx, p0.y + dy, p1.x + dx, p1.y + dy, paint)


def _draw_stippled(canvas: skia.Canvas, stroke: RenderStroke) -> None:
    """Stamp grain sprites along the stroke (pencil family).

    The device draws these pens as pure black at pressure-dependent
    density; we walk the polyline at fixed arc-length steps and stamp a
    stipple disk scaled to the local nib width.
    """
    from .textures import stipple_bank

    bank = stipple_bank()
    dx, dy = stroke.offset
    paint = skia.Paint(
        AntiAlias=True,
        ColorFilter=skia.ColorFilters.Blend(
            skia.Color(*stroke.rgba[:3], stroke.rgba[3]), skia.BlendMode.kSrcIn
        ),
    )
    sampling = skia.SamplingOptions(skia.FilterMode.kLinear)

    spatter = pens.spatter(stroke.tool)

    def stamp(x: float, y: float, nib: float, coverage: float) -> None:
        r = max(nib / 2, 0.4)
        if spatter is not None:
            w_mult, c_mult = spatter
            canvas.drawImageRect(
                bank.get(coverage * c_mult),
                skia.Rect.MakeLTRB(
                    x - r * w_mult, y - r * w_mult, x + r * w_mult, y + r * w_mult
                ),
                sampling,
                paint,
            )
        canvas.drawImageRect(
            bank.get(coverage),
            skia.Rect.MakeLTRB(x - r, y - r, x + r, y + r),
            sampling,
            paint,
        )

    points = stroke.points
    if len(points) == 1:
        p = points[0]
        cov = pens.stipple_coverage(stroke.tool, p.pressure)
        stamp(p.x + dx, p.y + dy, pens.nib_px(stroke.tool, p), cov)
        return

    residual = 0.0
    for p0, p1 in zip(points, points[1:]):
        seg_len = math.hypot(p1.x - p0.x, p1.y - p0.y)
        if seg_len == 0:
            continue
        nib0, nib1 = pens.nib_px(stroke.tool, p0), pens.nib_px(stroke.tool, p1)
        t = residual
        while t < seg_len:
            f = t / seg_len
            x = p0.x + (p1.x - p0.x) * f + dx
            y = p0.y + (p1.y - p0.y) * f + dy
            nib = nib0 * (1 - f) + nib1 * f
            pressure = p0.pressure * (1 - f) + p1.pressure * f
            stamp(x, y, nib, pens.stipple_coverage(stroke.tool, pressure))
            t += max(nib * 0.35, 0.7)
        residual = t - seg_len


def _draw_shader(canvas: skia.Canvas, stroke: RenderStroke) -> None:
    """Translucent ink with uniform per-stroke alpha.

    Drawn opaque into a transient layer composited once at SHADER_ALPHA:
    self-overlap within a stroke stays uniform, while separate strokes
    accumulate (matching the device, measured single/double coverage).
    """
    bounds = None  # full canvas; strokes are small, this is fine for now
    canvas.saveLayerAlpha(bounds, round(pens.SHADER_ALPHA * 255))
    _draw_ink(canvas, stroke)
    canvas.restore()


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
        if stroke.highlight:
            continue
        if pens.is_shader(stroke.tool):
            _draw_shader(canvas, stroke)
        elif pens.is_stippled(stroke.tool):
            _draw_stippled(canvas, stroke)
        else:
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
