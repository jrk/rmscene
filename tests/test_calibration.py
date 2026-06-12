"""Golden-image regression tests against official device renders.

Thresholds are locked from the calibrated state (see
notes/renderer_research.md); they have margin for minor AA differences
across skia versions but will catch real regressions in pen models.
"""

import pathlib

import pytest

skia = pytest.importorskip("skia")
np = pytest.importorskip("numpy")

from rmrender.calibrate import notebook_pages, reference_renders, run_page

BASE = pathlib.Path(__file__).parent / "data" / "render_calibration"

# name -> (min ssim, max mae_blur, min ink_iou)
THRESHOLDS = {
    "01-ballpoint": (0.995, 9, 0.95),
    "02-fineliner": (0.995, 9, 0.95),
    "03-pencil": (0.950, 45, 0.38),
    "04-mechanical_pencil": (0.985, 18, 0.80),
    "05-calligraphy": (0.995, 9, 0.94),
    "06-marker": (0.990, 15, 0.89),
    "07-shader": (0.985, 7, 0.97),
    "08-paintbrush": (0.975, 18, 0.74),
    "09-highlighter": (0.990, 5, 0.95),
    "10-eraser": (0.995, 4, 0.96),
    "11-erase_area": (0.995, 4, 0.97),
    "12-move_scale": (0.990, 9, 0.94),
    "13-layers": (0.995, 5, 0.93),
}


def _pages():
    pages = notebook_pages(BASE / "notebook")
    refs = reference_renders(BASE / "rendered")
    assert len(pages) == len(refs)
    return list(zip(refs, pages))


@pytest.mark.parametrize(
    "ref_png,rm_path", _pages(), ids=[r.stem for r, _ in _pages()]
)
def test_calibration_page(ref_png, rm_path, tmp_path):
    result = run_page(rm_path, ref_png, tmp_path, ref_png.stem)
    min_ssim, max_mae_blur, min_iou = THRESHOLDS[ref_png.stem]
    assert result["ssim"] >= min_ssim, result
    assert result["mae_blur"] <= max_mae_blur, result
    assert result["ink_iou"] >= min_iou, result


def test_jrk_test_page(tmp_path):
    # Paper Pro page: ballpoint + calligraphy + highlighter.
    data = BASE.parent
    result = run_page(
        data / "jrk_test.rm", data / "jrk_test.png", tmp_path, "jrk_test"
    )
    assert result["ssim"] >= 0.997, result
    assert result["ink_iou"] >= 0.97, result
    assert result["mae_blur"] <= 7, result
