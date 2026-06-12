"""Golden-image regression tests against official device renders.

Thresholds are locked from the calibrated state (see
notes/renderer_research.md); they have margin for minor AA differences
across skia versions but will catch real regressions in pen models.
"""

import pathlib

import pytest

skia = pytest.importorskip("skia")
np = pytest.importorskip("numpy")

from rmrender.calibrate import notebook_pages, run_page

BASE = pathlib.Path(__file__).parent / "data" / "render_calibration"

# name -> (min ssim, max mae_blur)
THRESHOLDS = {
    "01-ballpoint": (0.995, 8),
    "02-fineliner": (0.995, 6),
    "03-pencil": (0.950, 45),
    "04-mechanical_pencil": (0.985, 18),
    "05-calligraphy": (0.995, 7),
    "06-marker": (0.990, 15),
    "07-shader": (0.985, 7),
    "08-paintbrush": (0.975, 18),
    "09-highlighter": (0.990, 5),
    "10-eraser": (0.995, 4),
    "11-erase_area": (0.995, 4),
    "12-move_scale": (0.990, 8),
    "13-layers": (0.995, 5),
}


def _pages():
    pages = notebook_pages(BASE / "notebook")
    refs = sorted((BASE / "rendered").glob("*.png"))
    assert len(pages) == len(refs)
    return list(zip(refs, pages))


@pytest.mark.parametrize(
    "ref_png,rm_path", _pages(), ids=[r.stem for r, _ in _pages()]
)
def test_calibration_page(ref_png, rm_path, tmp_path):
    result = run_page(rm_path, ref_png, tmp_path, ref_png.stem)
    min_ssim, max_mae_blur = THRESHOLDS[ref_png.stem]
    assert result["ssim"] >= min_ssim, result
    assert result["mae_blur"] <= max_mae_blur, result
