"""Golden tests for the vector backends (PDF, SVG).

Each backend's output is rasterized back and compared against the
official device render, like the raster goldens but with slightly
looser thresholds to absorb third-party rasterizer AA differences.
"""

import io
import pathlib

import pytest

skia = pytest.importorskip("skia")
np = pytest.importorskip("numpy")

from PIL import Image

from rmrender import render_pdf, render_svg
from rmrender.calibrate import metrics, notebook_pages, reference_renders

BASE = pathlib.Path(__file__).parent / "data" / "render_calibration"

# name -> (min ssim, min ink_iou)
PDF_THRESHOLDS = {
    "01-ballpoint": (0.995, 0.88),
    "07-shader": (0.980, 0.94),
    "09-highlighter": (0.990, 0.94),
    "13-layers": (0.995, 0.93),
}
SVG_THRESHOLDS = {
    "01-ballpoint": (0.995, 0.94),
    "07-shader": (0.985, 0.96),
    "09-highlighter": (0.990, 0.95),
    "13-layers": (0.995, 0.93),
}


def _page_map():
    pages = notebook_pages(BASE / "notebook")
    refs = reference_renders(BASE / "rendered")
    return {ref.stem: (ref, rm) for ref, rm in zip(refs, pages)}


def _ref_array(ref_png):
    return np.asarray(Image.open(ref_png).convert("RGB"))


@pytest.mark.parametrize("name", sorted(PDF_THRESHOLDS))
def test_pdf_page(name, tmp_path):
    fitz = pytest.importorskip("fitz")
    ref_png, rm_path = _page_map()[name]
    ref = _ref_array(ref_png)
    h, w = ref.shape[:2]
    out = tmp_path / f"{name}.pdf"
    render_pdf(str(rm_path), str(out))
    doc = fitz.open(out)
    pix = doc[0].get_pixmap(
        matrix=fitz.Matrix(w / doc[0].rect.width, h / doc[0].rect.height)
    )
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    m = metrics(ref, img[:, :, :3])
    min_ssim, min_iou = PDF_THRESHOLDS[name]
    assert m["ssim"] >= min_ssim, m
    assert m["ink_iou"] >= min_iou, m


@pytest.mark.parametrize("name", sorted(SVG_THRESHOLDS))
def test_svg_page(name, tmp_path):
    cairosvg = pytest.importorskip("cairosvg")
    ref_png, rm_path = _page_map()[name]
    ref = _ref_array(ref_png)
    h, w = ref.shape[:2]
    out = tmp_path / f"{name}.svg"
    render_svg(str(rm_path), str(out))
    png_bytes = cairosvg.svg2png(
        url=str(out), output_width=w, output_height=h, background_color="white"
    )
    img = np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGB"))
    m = metrics(ref, img)
    min_ssim, min_iou = SVG_THRESHOLDS[name]
    assert m["ssim"] >= min_ssim, m
    assert m["ink_iou"] >= min_iou, m
