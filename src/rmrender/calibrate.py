"""Calibration harness: compare rmrender output against official device renders.

Usage:
    python -m rmrender.calibrate NOTEBOOK_DIR RENDERED_DIR -o OUT_DIR

NOTEBOOK_DIR is a raw synced notebook directory containing
`<uuid>.content` and `<uuid>/<page-uuid>.rm`; RENDERED_DIR contains the
official device renders, alphabetically ordered to match the notebook's
page order (e.g. `01-ballpoint.png`, `02-fineliner.png`, ...).

For each page this renders the .rm at the reference PNG's resolution and
reports SSIM / MAE / ink-mask IoU, writing a contact sheet
(reference | ours | diff) per page plus a metrics.json.
"""

import argparse
import json
import logging
import pathlib
import typing as tp

import numpy as np
import skia
from PIL import Image

from rmscene import read_tree

from .render import render_scene
from .scene import extract_strokes, page_size

_logger = logging.getLogger(__name__)


def render_array(rm_path: pathlib.Path, out_w: int, out_h: int) -> np.ndarray:
    """Render a page to an RGB uint8 array of the given size."""
    with open(rm_path, "rb") as f:
        tree = read_tree(f)
    strokes = extract_strokes(tree)
    page_w, page_h = page_size(tree)

    surface = skia.Surface(out_w, out_h)
    canvas = surface.getCanvas()
    canvas.clear(skia.ColorWHITE)
    canvas.scale(out_w / page_w, out_h / page_h)
    render_scene(canvas, strokes, shift_x=page_w / 2)
    rgba = surface.makeImageSnapshot().toarray(
        colorType=skia.ColorType.kRGBA_8888_ColorType
    )
    return rgba[:, :, :3]


def _to_gray(rgb: np.ndarray) -> np.ndarray:
    return rgb.astype(np.float64) @ [0.299, 0.587, 0.114]


def _box_filter(x: np.ndarray, w: int) -> np.ndarray:
    """Mean filter with a w x w window via cumulative sums ("valid" region)."""
    c = np.cumsum(np.cumsum(x, axis=0), axis=1)
    c = np.pad(c, ((1, 0), (1, 0)))
    return (
        c[w:, w:] - c[:-w, w:] - c[w:, :-w] + c[:-w, :-w]
    ) / (w * w)


def ssim(a: np.ndarray, b: np.ndarray, window: int = 8) -> float:
    """Mean SSIM over grayscale images (uniform window)."""
    C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a, mu_b = _box_filter(a, window), _box_filter(b, window)
    var_a = _box_filter(a * a, window) - mu_a * mu_a
    var_b = _box_filter(b * b, window) - mu_b * mu_b
    cov = _box_filter(a * b, window) - mu_a * mu_b
    num = (2 * mu_a * mu_b + C1) * (2 * cov + C2)
    den = (mu_a**2 + mu_b**2 + C1) * (var_a + var_b + C2)
    return float(np.mean(num / den))


INK_THRESHOLD = 250  # gray level below which a pixel counts as ink


def metrics(ref: np.ndarray, ours: np.ndarray) -> dict:
    g_ref, g_ours = _to_gray(ref), _to_gray(ours)
    ink_ref = g_ref < INK_THRESHOLD
    ink_ours = g_ours < INK_THRESHOLD
    union = ink_ref | ink_ours
    iou = float((ink_ref & ink_ours).sum() / union.sum()) if union.any() else 1.0
    mae_ink = (
        float(np.abs(g_ref[union] - g_ours[union]).mean()) if union.any() else 0.0
    )
    # Density comparison on blurred images: fair to dithered pens, where
    # the stipple phase cannot match pixel-for-pixel.
    b_ref, b_ours = _box_filter(g_ref, 5), _box_filter(g_ours, 5)
    b_union = _box_filter(union.astype(np.float64), 5) > 0
    mae_blur = (
        float(np.abs(b_ref[b_union] - b_ours[b_union]).mean())
        if b_union.any()
        else 0.0
    )
    return {
        "ssim": round(ssim(g_ref, g_ours), 4),
        "mae_gray": round(float(np.abs(g_ref - g_ours).mean()), 3),
        "mae_ink": round(mae_ink, 1),
        "mae_blur": round(mae_blur, 1),
        "ink_iou": round(iou, 4),
        "ink_px_ref": int(ink_ref.sum()),
        "ink_px_ours": int(ink_ours.sum()),
    }


def contact_sheet(
    ref: np.ndarray, ours: np.ndarray, out_path: pathlib.Path
) -> None:
    """Write [reference | ours | diff] side by side."""
    diff = np.abs(_to_gray(ref) - _to_gray(ours))
    heat = np.full((*diff.shape, 3), 255, np.uint8)
    heat[..., 1] = heat[..., 2] = (255 - diff).clip(0, 255).astype(np.uint8)
    gap = np.full((ref.shape[0], 8, 3), 128, np.uint8)
    sheet = np.concatenate([ref, gap, ours, gap, heat], axis=1)
    Image.fromarray(sheet).save(out_path)


def run_page(
    rm_path: pathlib.Path,
    ref_png: pathlib.Path,
    out_dir: pathlib.Path,
    name: str,
) -> dict:
    ref = np.asarray(Image.open(ref_png).convert("RGB"))
    ours = render_array(rm_path, ref.shape[1], ref.shape[0])
    m = metrics(ref, ours)
    m["name"] = name
    out_dir.mkdir(parents=True, exist_ok=True)
    contact_sheet(ref, ours, out_dir / f"{name}.sheet.png")
    return m


def notebook_pages(notebook_dir: pathlib.Path) -> list[pathlib.Path]:
    """Page .rm files of a raw notebook directory, in page order."""
    content_files = list(notebook_dir.glob("*.content"))
    if len(content_files) != 1:
        raise ValueError(f"Expected one .content file in {notebook_dir}")
    content = json.loads(content_files[0].read_text())
    doc_id = content_files[0].stem
    page_ids = [p["id"] for p in content["cPages"]["pages"] if "deleted" not in p]
    return [notebook_dir / doc_id / f"{pid}.rm" for pid in page_ids]


def run_notebook(
    notebook_dir: pathlib.Path,
    rendered_dir: pathlib.Path,
    out_dir: pathlib.Path,
) -> list[dict]:
    pages = notebook_pages(notebook_dir)
    refs = sorted(rendered_dir.glob("*.png"))
    if len(pages) != len(refs):
        raise ValueError(
            f"{len(pages)} pages but {len(refs)} reference renders"
        )
    results = []
    for rm_path, ref_png in zip(pages, refs):
        result = run_page(rm_path, ref_png, out_dir, ref_png.stem)
        _logger.info("%s", result)
        results.append(result)
    (out_dir / "metrics.json").write_text(json.dumps(results, indent=1))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("notebook_dir", type=pathlib.Path)
    parser.add_argument("rendered_dir", type=pathlib.Path)
    parser.add_argument("-o", "--out", type=pathlib.Path, default=pathlib.Path("calibration_out"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    results = run_notebook(args.notebook_dir, args.rendered_dir, args.out)
    width = max(len(r["name"]) for r in results)
    for r in results:
        print(
            f"{r['name']:{width}s}  ssim={r['ssim']:.4f}  ink_iou={r['ink_iou']:.4f}"
            f"  mae_ink={r['mae_ink']:5.1f}  mae_blur={r['mae_blur']:5.1f}"
            f"  ink_px ref/ours={r['ink_px_ref']}/{r['ink_px_ours']}"
        )


if __name__ == "__main__":
    main()
