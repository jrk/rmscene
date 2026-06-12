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

### M2 — calibration harness (next up; the keystone)
`calibrate/` tool: render a `.rm` at the official PNG's resolution, align,
produce per-pixel diff + SSIM + side-by-side contact sheets; lock scores as
golden regression tests. Every fidelity change after this is measured, not
eyeballed. First golden: `jrk_test`. Blocked only on calibration captures
(see "Captures needed" below) for full tool coverage.

### M3 — intensity & texture models (biggest visible win)
- Ballpoint per-segment intensity (start `pressure_n^5 + 0.7`, refit
  against captures); decide gray vs dither grain from close-ups.
- Procedural grain sprites (license-clean, remy-style) for pencil /
  mechanical pencil / paintbrush; spatter second pass; paintbrush sprite
  rotation to `direction + 90°`.
- Switch raster ink to arc-length stamping: smooth width interpolation
  between points, `starting_length` as texture phase. Fixes width stepping.
- Shader semantics from its capture (accumulate on overlap or not?).

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

## Captures needed (action: jrk)

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

Still open — all answered by captures above except the last two:
1. Ballpoint intensity curve and grain type (gray vs dither).
2. Shader overlap semantics.
3. Legacy highlight pastel color.
4. Eraser-on-template behavior (white ink vs true clip).
5. Whether lasso-scale rescales stored nib widths.
6. Does xochitl smooth between sampled points? (compare close-ups at high
   zoom; affects M3 stamping spacing and M4 outline quality)
7. skia-python no-skia fallback (pure-PIL stamp renderer) — decide if/when
   someone needs an environment without the wheel.
8. Performance target for batch export — revisit at M4; vectorize stamping
   with numpy or `drawAtlas` only if needed.
