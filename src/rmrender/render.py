"""Rendering of v6 scenes with skia-python: raster (PNG) and vector (PDF, SVG).

Strategy (see notes/renderer_plan.md):

- Ink strokes: one antialiased round-capped line segment per point pair,
  width averaged between the endpoints' device-stored nib widths;
  ballpoint "railroading" rails below the light-pressure threshold.
- Highlighter: drawn first, globally under all ink, fully opaque --
  overlapping highlights merge into a flat union, ink stays on top.
- Pencil family: raster mode stamps stipple sprites (matching the
  device's pure-black dither); vector mode falls back to opaque
  paper-blended gray strokes.
- Shader: per-stroke uniform alpha that accumulates across strokes --
  saveLayerAlpha in raster mode, a single unioned outline path with a
  translucent fill in vector mode.
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
        w = (pens.nib_px(stroke.tool, p0) + pens.nib_px(stroke.tool, p1)) / 2
        hole = pens.railroad_hole(
            stroke.tool, (p0.pressure + p1.pressure) / 2
        )
        if hole <= 0:
            paint.setStrokeWidth(w)
            canvas.drawLine(p0.x + dx, p0.y + dy, p1.x + dx, p1.y + dy, paint)
            continue
        # Railroading: two dark rails with a faded core streak.
        sx, sy = p1.x - p0.x, p1.y - p0.y
        length = math.hypot(sx, sy)
        if length == 0:
            paint.setStrokeWidth(w)
            canvas.drawLine(p0.x + dx, p0.y + dy, p1.x + dx, p1.y + dy, paint)
            continue
        nx, ny = -sy / length * w * 0.30, sx / length * w * 0.30
        paint.setStrokeWidth(w * 0.42)
        for ox, oy in ((nx, ny), (-nx, -ny)):
            canvas.drawLine(
                p0.x + dx + ox, p0.y + dy + oy,
                p1.x + dx + ox, p1.y + dy + oy, paint,
            )
        r, g, b, a = stroke.rgba
        core = _paint((r, g, b, round(a * (1 - hole))))
        core.setStrokeCap(skia.Paint.kRound_Cap)
        core.setStrokeWidth(w * 0.36)
        canvas.drawLine(p0.x + dx, p0.y + dy, p1.x + dx, p1.y + dy, core)


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


def _stroke_outline(stroke: RenderStroke) -> skia.Path:
    """Union of the stroke's per-segment stroked outlines: one fill path
    covering the variable-width ribbon, uniform even where it
    self-overlaps."""
    dx, dy = stroke.offset
    points = stroke.points
    builder = skia.OpBuilder()
    paint = skia.Paint(
        Style=skia.Paint.kStroke_Style, StrokeCap=skia.Paint.kRound_Cap
    )
    if len(points) == 1:
        p = points[0]
        dot = skia.Path()
        dot.addCircle(p.x + dx, p.y + dy, pens.nib_px(stroke.tool, p) / 2)
        builder.add(dot, skia.PathOp.kUnion_PathOp)
        return builder.resolve()
    for p0, p1 in zip(points, points[1:]):
        seg = skia.Path()
        seg.moveTo(p0.x + dx, p0.y + dy)
        seg.lineTo(p1.x + dx, p1.y + dy)
        paint.setStrokeWidth(
            (pens.nib_px(stroke.tool, p0) + pens.nib_px(stroke.tool, p1)) / 2
        )
        fill = skia.Path()
        paint.getFillPath(seg, fill)
        builder.add(fill, skia.PathOp.kUnion_PathOp)
    return builder.resolve()


def _draw_shader_vector(canvas: skia.Canvas, stroke: RenderStroke) -> None:
    """Vector-safe shader: one unioned outline filled translucently.

    Equivalent to the raster saveLayerAlpha approach (uniform within a
    stroke, accumulating across strokes) but expressible in PDF/SVG
    without layers."""
    r, g, b, a = stroke.rgba
    paint = skia.Paint(
        AntiAlias=True,
        Color=skia.Color(r, g, b, round(a * pens.SHADER_ALPHA)),
        Style=skia.Paint.kFill_Style,
    )
    canvas.drawPath(_stroke_outline(stroke), paint)


def _draw_stippled_vector(canvas: skia.Canvas, stroke: RenderStroke) -> None:
    """Vector fallback for the pencil family: opaque paper-blended gray.

    The raster stipple cannot be carried into PDF/SVG without embedding
    images; instead each run of similar coverage becomes a stroke whose
    color is the ink blended toward paper by (1 - coverage). Opaque
    paint keeps crossings from compounding.
    """
    dx, dy = stroke.offset
    points = stroke.points
    r, g, b, a = stroke.rgba

    def gray(coverage: float) -> skia.Paint:
        cr = round(255 - (255 - r) * coverage)
        cg = round(255 - (255 - g) * coverage)
        cb = round(255 - (255 - b) * coverage)
        p = skia.Paint(
            AntiAlias=True,
            Color=skia.Color(cr, cg, cb, a),
            Style=skia.Paint.kStroke_Style,
        )
        p.setStrokeCap(skia.Paint.kRound_Cap)
        return p

    if len(points) == 1:
        p = points[0]
        cov = pens.stipple_coverage(stroke.tool, p.pressure)
        dot = gray(cov)
        dot.setStyle(skia.Paint.kFill_Style)
        canvas.drawCircle(p.x + dx, p.y + dy, pens.nib_px(stroke.tool, p) / 2, dot)
        return
    for p0, p1 in zip(points, points[1:]):
        cov = pens.stipple_coverage(
            stroke.tool, (p0.pressure + p1.pressure) / 2
        )
        paint = gray(cov)
        paint.setStrokeWidth(
            (pens.nib_px(stroke.tool, p0) + pens.nib_px(stroke.tool, p1)) / 2
        )
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
    canvas: skia.Canvas,
    strokes: list[RenderStroke],
    shift_x: float,
    vector: bool = False,
) -> None:
    """Draw strokes onto any skia canvas (raster, PDF, or SVG).

    `vector` selects layer-free, image-free drawing for the pens whose
    raster path uses saveLayer or sprite stamping.
    """
    canvas.translate(shift_x, 0)
    # Highlights form a global background layer under all ink.
    for stroke in strokes:
        if stroke.highlight:
            _draw_highlight(canvas, stroke)
    for stroke in strokes:
        if stroke.highlight:
            continue
        if pens.is_shader(stroke.tool):
            (_draw_shader_vector if vector else _draw_shader)(canvas, stroke)
        elif pens.is_stippled(stroke.tool):
            (_draw_stippled_vector if vector else _draw_stippled)(canvas, stroke)
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


# Device pixels per inch; reMarkable 2 is 226, Paper Pro is 229. Used to
# size PDF pages in points.
SCREEN_DPI = 226


def render_pdf(rm_path: str, pdf_path: str, dpi: float = SCREEN_DPI) -> tuple[float, float]:
    """Render `rm_path` to a single-page vector PDF.

    Returns the page size in points.
    """
    with open(rm_path, "rb") as f:
        tree = read_tree(f)
    strokes = extract_strokes(tree)
    page_w, page_h = page_size(tree)
    pt = 72.0 / dpi
    w_pt, h_pt = page_w * pt, page_h * pt

    stream = skia.FILEWStream(pdf_path)
    doc = skia.PDF.MakeDocument(stream)
    canvas = doc.beginPage(w_pt, h_pt)
    canvas.scale(pt, pt)
    render_scene(canvas, strokes, shift_x=page_w / 2, vector=True)
    doc.endPage()
    doc.close()
    stream.flush()
    _logger.info("Rendered %d strokes to %s (%.0fx%.0f pt)", len(strokes), pdf_path, w_pt, h_pt)
    return w_pt, h_pt


def render_svg(rm_path: str, svg_path: str) -> tuple[int, int]:
    """Render `rm_path` to an SVG sized in page pixels."""
    with open(rm_path, "rb") as f:
        tree = read_tree(f)
    strokes = extract_strokes(tree)
    page_w, page_h = page_size(tree)

    stream = skia.FILEWStream(svg_path)
    canvas = skia.SVGCanvas.Make(skia.Rect.MakeWH(page_w, page_h), stream)
    render_scene(canvas, strokes, shift_x=page_w / 2, vector=True)
    del canvas  # finalize the SVG document
    stream.flush()
    _logger.info("Rendered %d strokes to %s (%dx%d)", len(strokes), svg_path, page_w, page_h)
    return page_w, page_h
