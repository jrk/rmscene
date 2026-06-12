"""rmrender: a faithful renderer for reMarkable v6 scenes.

M0 scope: raster (PNG) output via skia-python, geometry driven by the
per-point nib widths stored in the file. See notes/renderer_plan.md.
"""

from .render import render_pdf, render_png, render_svg

__all__ = ["render_png", "render_pdf", "render_svg"]
