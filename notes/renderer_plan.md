# Plan: a Skia-based faithful renderer for reMarkable v6 files

Companion to `renderer_research.md`. Goal: render `.rm` v6 scenes with
near-native fidelity to **bitmap (PNG), PDF, and SVG** from one code path,
using rmscene as the parser.

## Status (updated 2026-06-12)

**Done — M0 + M1, shipped as `src/rmrender/` in this repo:**

- Decisions taken: standalone package in this repo; Python + skia-python
  (optional `render` extra in pyproject); highlights are a **global**
  background layer (not per-layer), per jrk.
- IR extraction from `SceneTree` (`scene.py`): document-order strokes,
  group visibility, top/bottom special anchors, page size from
  `SceneInfo.paper_size` (Paper Pro 1620×2160 confirmed working).
- Pen table (`pens.py`): geometry from device-stored nib width
  (`point.width / 4`) with RCU multipliers; palette + `color_rgba`
  resolution; eraser = white ink; erase-area skipped.
- Raster backend (`render.py`): per-segment round-capped AA strokes;
  highlighter as opaque flat-union background under all ink; CLI
  `python -m rmrender in.rm out.png --scale N`; smoke tests in
  `tests/test_render.py`.
- Validated by eye against `tests/data/jrk_test.png`: geometry, calligraphy
  nib, ballpoint tapers, and the 30 px highlight union all match well.
- **Moved strokes: resolved, nothing to do.** Moving rewrites point
  coordinates into a new item; `move_id` is provenance only (research
  notes §4). Renderers get moves right by walking the visible tree.

**Known gaps vs native (the remaining work):** no ballpoint intensity fade
at light pressure (tails render slightly heavy); width steps per segment
instead of smooth interpolation at high zoom; no pencil/paintbrush grain;
no vector outputs yet; typed text and text-anchored groups unrendered.

## Next milestones

### M2 — calibration harness ✅ DONE (2026-06-12)
`rmrender/calibrate.py`: renders each notebook page at the official PNG's
resolution, reports SSIM / ink-IoU / MAE / blurred-density MAE, writes
[ref | ours | diff] contact sheets and metrics.json. CLI:
`python -m rmrender.calibrate NOTEBOOK_DIR RENDERED_DIR -o OUT`.
Golden regression tests in `tests/test_calibration.py` over the 13-page
capture suite in `tests/data/render_calibration/` (raw synced notebook +
official renders, all 1404x1872 1:1).

Findings from the capture suite (also in renderer_research.md):
- Eraser and erase-area edits are **fully baked at edit time** — no
  eraser strokes persist in saved files. Nothing to render.
- Lasso-scale **re-bakes stored point widths** (and coordinates);
  `thickness_scale` changes but stays renderer-irrelevant.
- Pencil / mech pencil / paintbrush official ink is **pure black binary
  stipple** (density varies, never gray).
- Shader is translucent (single coverage gray ≈187/255 → alpha 0.235)
  and **accumulates across strokes**, unlike highlighter; uniform within
  a stroke.
- Two-layer page matches the global-background highlight model.

### M3 — intensity & texture models ✅ MOSTLY DONE (2026-06-12)
Implemented and calibrated against the captures:
- Pencil + mech pencil: arc-length stamped procedural stipple disks
  (`textures.py`, grain=2 clumps), coverage curves in `pens.py`
  (pencil `0.07 + 0.83·p^1.6`, mech `0.45 + 0.6·p`, mech nib 0.9 —
  RCU's 1/1.5 was wrong for v6).
- Shader: per-stroke saveLayerAlpha at 0.235 (uniform within stroke,
  accumulates across strokes). SSIM 0.991.
- Paintbrush: pressure narrows nib below stored width (clamped RCU
  formula). SSIM 0.983.

Remaining M3 polish (diminishing returns, revisit on demand):
- Pencil grain *character*: official strokes have a softer, fuzzier
  spread (mae_blur plateaus ~34 regardless of density) — likely needs
  the RCU "spatter" second pass and/or directional grain.
- Ballpoint light-pressure intensity fade (tails slightly heavy; page
  already at SSIM 0.999 / mae_blur 5).
- Marker slight over-width (mae_blur 11).

### M4 — vector backends
- PDF first (Skia PDF canvas reuses existing drawing; `kDarken` for
  highlights over PDF backgrounds; optional true `/Highlight` annotations
  from union geometry).
- SVG: variable-width strokes as filled outline paths (offset-curve
  ribbons); highlights pre-unioned via Skia pathops into one opaque
  `<path>` per color placed before ink — correct in any viewer without
  blend-mode support. Path simplification for file size.
- Vector texture pens: intensity-as-color + `kDarken` (RCU's approach).

### M5 — edge cases
- Text-anchored groups + typed text rendering (root text).
- `GlyphRange` smart-highlight rectangles (annotated PDFs).
- Accurate eraser via clip paths (needed over templates/PDF backgrounds).
- Selection-*scale* behavior: confirm stored widths rescale (one capture).

## Captures needed (action: jrk) — ✅ RECEIVED 2026-06-12

Delivered as a 13-page raw synced notebook + official renders in
`tests/data/render_calibration/{notebook,rendered}/` (covers everything
below except `calib_highlight_legacy`). Kept for reference:

For each item: one notebook page on the device, exported two ways with
matching content — the raw `.rm` page file and the official PNG render
(same export pipeline used for `jrk_test.png`, which came out 1:1 at
1620×2160). Drop them in **`tests/data/calibration/`** with these names:

| Files (`.rm` + `.png`) | Page content |
|---|---|
| `calib_ballpoint.*` | One tool per page, same recipe for each: 3 brush sizes; for each size a slow heavy stroke, a fast light stroke, a pressure ramp (press harder along the stroke), a fast scribble, crossings/self-overlap |
| `calib_fineliner.*` | same recipe |
| `calib_pencil.*` | same recipe (tilt the pen on some strokes) |
| `calib_mech_pencil.*` | same recipe |
| `calib_marker.*` | same recipe |
| `calib_paintbrush.*` | same recipe (vary speed a lot — speed strongly affects it) |
| `calib_calligraphy.*` | same recipe (vary stroke direction: loops, hatching) |
| `calib_shader.*` | same recipe **plus** heavily overlapping strokes — settles whether shader accumulates where strokes cross (highlighter doesn't) |
| `calib_highlighter.*` | every highlight color; overlapping same-color strokes; overlapping different-color strokes; highlight drawn over ink AND ink drawn over highlight; tight back-and-forth fill of an area |
| `calib_highlight_legacy.*` | only if an old pre-3.x notebook exists with yellow highlights (no per-color support) — pins the legacy pastel |
| `calib_eraser.*` | ink strokes partially erased with the eraser tool (not erase-area, not undo) |
| `calib_layers.*` | two layers: layer 1 ink under layer 2 highlight, and layer 1 highlight under layer 2 ink — verifies the global-background assumption |
| `calib_move_scale.*` | strokes lasso-moved and lasso-**scaled** (bigger and smaller) — verifies scaling re-bakes nib widths |

Notes: keep pages uncluttered (one tool per page); black ink is fine except
where color is the point; don't rename pages after export so `.rm` and
`.png` stay paired. If the export pipeline offers a resolution choice, use
the same one every time.

Priority order if capturing incrementally: `calib_ballpoint`,
`calib_highlighter`, `calib_layers` (unblocks M3 intensity + confirms the
global-background decision), then the texture pens, then the rest.

## Open questions (updated)

Resolved: package location (standalone, this repo); highlight scope
(global background); language (Python/skia-python); `move_id` (provenance
only, no renderer work).

Newly resolved by the capture suite: shader accumulates across strokes
(α=0.235); eraser/erase-area are baked at edit time (nothing to render,
even over templates); lasso-scale re-bakes stored widths.

Still open:
1. Ballpoint light-pressure fade curve (minor; page already SSIM 0.999).
2. Legacy highlight pastel color (needs an old pre-3.x notebook).
3. Pencil grain character (spatter pass / directional grain).
4. Does xochitl smooth between sampled points? (affects M4 outline quality)
5. skia-python no-skia fallback (pure-PIL stamp renderer) — decide if/when
   someone needs an environment without the wheel.
6. Performance target for batch export — revisit at M4; vectorize stamping
   with numpy or `drawAtlas` only if needed.
