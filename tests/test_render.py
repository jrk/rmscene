import pathlib

import pytest

skia = pytest.importorskip("skia")

from rmrender import render_png
from rmrender.scene import extract_strokes, page_size
from rmscene import read_tree

DATA_PATH = pathlib.Path(__file__).parent / "data"


def test_extract_strokes_jrk_test():
    with open(DATA_PATH / "jrk_test.rm", "rb") as f:
        tree = read_tree(f)
    strokes = extract_strokes(tree)
    assert len(strokes) == 101
    assert page_size(tree) == (1620, 2160)
    highlights = [s for s in strokes if s.highlight]
    assert len(highlights) == 1
    assert highlights[0].rgba == (190, 234, 254, 255)


def test_render_png_smoke(tmp_path):
    out = tmp_path / "out.png"
    w, h = render_png(str(DATA_PATH / "jrk_test.rm"), str(out), scale=1.0)
    assert (w, h) == (1620, 2160)
    assert out.stat().st_size > 10000


@pytest.mark.parametrize(
    "name",
    ["Color_and_tool_v3.14.4.rm", "More_color_highlight_shader_v3.15.4.2.rm", "Lines_v2.rm"],
)
def test_render_other_files(tmp_path, name):
    out = tmp_path / "out.png"
    render_png(str(DATA_PATH / name), str(out))
    assert out.exists()
