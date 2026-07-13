"""Extract a flat, render-ready stroke list from an rmscene SceneTree."""

import logging
import typing as tp
from dataclasses import dataclass

from rmscene import CrdtId, SceneTree
from rmscene import scene_items as si

from . import pens

_logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = (1404, 1872)

# Special anchor ids: groups pinned to the top/bottom of the page.
ANCHOR_TOP = CrdtId(0, 281474976710654)
ANCHOR_BOTTOM = CrdtId(0, 281474976710655)


@dataclass
class RenderStroke:
    tool: si.Pen
    rgba: tuple[int, int, int, int]
    points: list[si.Point]
    offset: tuple[float, float]  # group anchor translation
    highlight: bool


def page_size(tree: SceneTree) -> tuple[int, int]:
    info = getattr(tree, "scene_info", None)
    if info is not None:
        # Firmware 3.27+ also writes the size as doubles; prefer the
        # newer fields in case the legacy int pair stops being written.
        lww = getattr(info, "paper_size_lww", None)
        if lww is not None:
            w, h = lww.value
            return round(w), round(h)
        raw = getattr(info, "paper_size_raw", None)
        if raw is not None:
            return round(raw[0]), round(raw[1])
        if info.paper_size is not None:
            return info.paper_size
    return DEFAULT_PAGE_SIZE


def extract_strokes(tree: SceneTree) -> list[RenderStroke]:
    """Flatten the scene tree into document-order strokes.

    Group anchor translations are accumulated into per-stroke offsets.
    Text-anchored groups are not supported yet (M0) and fall back to the
    top of the page with a warning.
    """
    page_h = page_size(tree)[1]
    strokes: list[RenderStroke] = []

    def group_offset(group: si.Group) -> tuple[float, float]:
        if group.anchor_id is None:
            return (0.0, 0.0)
        assert group.anchor_origin_x is not None
        anchor_x = group.anchor_origin_x.value
        anchor = group.anchor_id.value
        if anchor == ANCHOR_TOP:
            return (anchor_x, 0.0)
        if anchor == ANCHOR_BOTTOM:
            return (anchor_x, float(page_h))
        _logger.warning("Text-anchored group %s not supported yet", anchor)
        return (anchor_x, 0.0)

    def walk(group: si.Group, dx: float, dy: float) -> None:
        if not group.visible.value:
            return
        for child_id in group.children:
            child = group.children[child_id]
            if isinstance(child, si.Group):
                gx, gy = group_offset(child)
                walk(child, dx + gx, dy + gy)
            elif isinstance(child, si.Line):
                if pens.should_skip(child.tool):
                    continue
                strokes.append(
                    RenderStroke(
                        tool=child.tool,
                        rgba=pens.resolve_color(
                            child.tool, child.color, child.color_rgba
                        ),
                        points=child.points,
                        offset=(dx, dy),
                        highlight=pens.is_highlight(child.tool),
                    )
                )
            # Text and GlyphRange items are not rendered in M0.

    walk(tree.root, 0.0, 0.0)
    return strokes
