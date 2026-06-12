# Plan: a Skia-based faithful renderer for reMarkable v6 files

Companion to `renderer_research.md`. Goal: render `.rm` v6 scenes with
near-native fidelity to **bitmap (PNG), PDF, and SVG** from one code path,
using rmscene as the parser.

## 0. Language and stack decision

**Python, using [skia-python](https://github.com/kyamagu/skia-python).**

Rationale:
- rmscene is Python; zero-friction integration and the whole existing
  ecosystem (rmc, remarks) is Python.
- Skia gives us, natively: high-quality AA raster surfaces, `SkPath` boolean
  ops (`pathops.Op.UNION` for highlight unions), blend modes
  (`BlendMode.kDarken` / `kMultiply`), shaders for texture stamping, and
  **PDF and SVG backends driven by the same canvas API** — bitmap and vector
  output from one renderer.
- Alternative considered: Rust (`skia-safe`) — better perf and packaging of a
  CLI binary, but loses the rmscene integration and iteration speed. Revisit
  only if Python perf is inadequate (unlikely: pages have ~10²–10³ strokes).

Risks / mitigations:
- skia-python wheel availability lags Skia releases and is a heavy binary
  (~80 MB). Mitigate: pin a known-good version; keep the renderer a separate
  optional package so rmscene stays dependency-light.
- SVG backend in Skia is the weakest of the three sinks (no blend-mode
  guarantee in consumers). Mitigate: for SVG, prefer *geometry-complete*
  output (outline fills, pre-unioned highlights) over blend modes (§4).

## 1. Package layout

New package (working name `rmrender`), either in a fork of rmc or standalone:

```
rmrender/
  scene.py        # rmscene SceneTree -> render IR
  pens.py         # pen models: width/intensity/texture per tool
  geometry.py     # polyline -> variable-width outline path; smoothing
  textures.py     # procedural grain sprite generation (MIT-clean)
  backends/
    raster.py     # skia.Surface -> PNG
    pdf.py        # skia.PDF document
    svg.py        # skia SVG canvas, or hand-rolled SVG writer (see §4)
  calibrate/      # golden-image comparison harness
```

## 2. Intermediate representation (IR)

Resolve all format quirks *before* drawing:

```python
@dataclass
class RenderPoint:
    x: float; y: float          # px, x already shifted by +screen_w/2
    nib: float                  # FINAL nib width in px = point.width/4 × pen multiplier
    intensity: float            # 0..1, from pen model (pressure/speed)
    direction: float            # radians

@dataclass
class RenderStroke:
    tool: Pen
    rgba: tuple                 # resolved color (palette or color_rgba)
    points: list[RenderPoint]
    z_class: int                # 0 = highlight underlay, 1 = ink
    blend: skia.BlendMode       # kSrcOver | kDarken (highlight/pencil-vector)
    arc_offset: float           # starting_length, for texture phase
```

Layer structure (groups), anchored-group transforms, and visibility flags are
preserved from `SceneTree` as in rmc's `draw_group`.

Page geometry: take size from `SceneInfo.paper_size` when present
(reMarkable Paper Pro differs from the 1404×1872 rM2 default); the x-shift is
`screen_width/2`.

## 3. Pen models (`pens.py`)

Single source of truth table (constants from RCU/remy/lines-are-rusty; see
research notes §3):

| Tool | nib(px) | intensity | texture | caps |
|---|---|---|---|---|
| Fineliner | `w/4` | 1 | — | round |
| Calligraphy | `w/4` | 1 | — | round |
| Ballpoint | `w/4` | `clamp(pressure_n^5 + 0.7)` (calibrate, see §7) | optional fine noise at intensity < 1 | round |
| Marker | `0.7·w/4` | 1 | — | round |
| Pencil (tilt) | `0.58·w/4` (+ spatter pass `1.25×`, intensity `0.7·p`) | `p` | grain sprite, log index `0.25·(p·N)^1.21` | round |
| Mech pencil | `(w/4)/1.5` | `p` | grain sprite, linear index | round |
| Paintbrush | `0.75·w/4 + (p−1)·0.5625·w/4` | `p·(2 − speed_n/75)` | grain sprite rotated to `direction+90°` | flat (round when segment shorter than nib) |
| Highlighter | constant 30 (or `w/4`, they agree) | 1, **opaque** | — | flat |
| Shader | `w/4` | low constant (calibrate) | — | round |
| Eraser | `w/4` | white ink or clip-out (§5) | — | square |
| Erase-area | skip (device ignores) | — | — | — |

`pressure_n = pressure/255`, `speed_n = speed/4` (v2 units → v1 scale).

## 4. Geometry and drawing strategy

### Ink strokes (variable width)
Two modes, selected per backend:

1. **Stamp mode (raster, max fidelity).** Walk the polyline at fixed
   arc-length spacing (≈ nib/3), interpolating position/nib/intensity between
   points; stamp an antialiased disk (or grain sprite) scaled to the local
   nib. `starting_length` seeds the arc-length counter so texture phase is
   continuous across continued strokes. This is structurally what xochitl
   does and trivially handles pencil grain and ballpoint dither.

2. **Outline mode (vector + fast raster).** Build a single closed path per
   stroke: offset each point `±nib/2` along the normal (normal from averaged
   segment directions), join left/right rails, cap with semicircles.
   One `fill` with nonzero winding → uniform color even where the ribbon
   self-overlaps; no chunk-joint artifacts. Optional: Catmull-Rom smoothing
   before offsetting; RDP simplification after, with curve fitting to keep
   SVG/PDF size sane.

Degenerate cases: single-point strokes → dot of diameter nib; zero-length
segments → skip; very sharp turns → miter limit via round joins on the rails.

### Highlights
- Resolve color: `color_rgba` if present, else legacy yellow
  `(255, 235, 74)`-ish — calibrate against device (§7).
- Build each stroke outline at constant 30 px, flat caps.
- **Union all outlines per (layer, color) with `skia.pathops UNION`** into one
  path. Draw it *first* in the layer (under ink), fully opaque, with
  `BlendMode.kDarken` so PDF/template backgrounds show through.
- SVG backend: emit the unioned path as a plain opaque `<path>` placed before
  ink elements (no blend-mode dependence); optionally add
  `style="mix-blend-mode:darken"` for viewers that honor it over backgrounds.
- PDF backend: Skia handles the blend mode; optionally also emit true
  `/Highlight` annotations from the same union geometry (RCU's trick) behind
  a flag.

### Texture pens on vector backends
Vector pencil/paintbrush: fall back to intensity-as-color (lighten toward
paper, RCU's vector mode) with `kDarken` so overlapping pencil strokes don't
double-darken. Optionally embed a tiling pattern fill later.

## 5. Eraser and erase-area

- v6 erasing usually mutates strokes at edit time, but `ERASER` strokes can
  persist. Default: paint with paper color, nib = `w/4`, square caps —
  matching device output on plain backgrounds.
- Accurate mode (later): remy-style — build eraser outline, clip *prior*
  strokes in the same layer against its complement. Required only when
  rendering over templates/PDF where white-ink is visible.
- `ERASER_AREA`: skip entirely (device ignores).

## 6. Procedural textures (`textures.py`)

License-clean replacement for RCU's PPM banks (AGPL — must not copy):
generate N≈16 grain sprites per bank: white-noise dot fields with coverage
increasing with bank index (remy: coverage `∝ (i+1)/N/2.5`, drawn at 0.4
scale), plus a paintbrush bank with directional streaks. Bank lookup:
linear for mech pencil, `0.25·(p·N)^1.21` for pencil/paintbrush. Sprites are
used as Skia shaders (local matrix = rotation to `direction+90°` for brush).
Calibrate against device renders; if procedural grain proves visibly off,
re-derive textures by *sampling our own device's PNG output* (clean-room,
license-safe).

## 7. Calibration & validation harness (`calibrate/`)

This is the core engineering loop — fidelity comes from comparison, not
guesswork:

1. **Calibration pages**: on-device notebooks, one per tool, sweeping
   pressure/speed/size/color (slow heavy stroke, fast light stroke, loops,
   crossings, overlapping highlights, highlight-over-ink and ink-over-
   highlight). Export official PNG + copy raw `.rm` (as done for
   `jrk_test.rm`).
2. **Harness**: render `.rm` at the official PNG's resolution; align (the
   official export geometry is deterministic); compare via per-pixel diff +
   SSIM; emit side-by-side contact sheets.
3. **Golden tests**: lock thresholds per page; CI regression on every change.
4. Fit the open parameters against the goldens: ballpoint intensity curve,
   shader opacity, highlight legacy color, texture grain density.

## 8. Milestones

- **M0 — skeleton**: IR from SceneTree; raster backend; all tools as
  outline-mode solid fills using stored widths; jrk_test renders with correct
  geometry. *(Already beats rmc on geometry.)*
- **M1 — highlights**: per-color union, under-ink ordering, kDarken;
  validates against jrk_test highlight blob.
- **M2 — calibration harness** + calibration pages from device.
- **M3 — intensity models**: ballpoint/pencil/paintbrush intensity; stamp
  mode for raster; procedural textures.
- **M4 — vector backends**: PDF (blend modes, optional annotations), SVG
  (outline fills, pre-unioned highlights); file-size guardrails
  (simplification/curve fitting).
- **M5 — edge cases**: erasers, anchored groups/text interplay, Paper Pro
  paper sizes, multi-author files, `move_id` transforms.

## 9. Open questions to resolve

1. **Where it lives**: standalone `rmrender` package vs. PR into rmc.
   Standalone recommended (rmc's maxio-derived pen code would be replaced
   wholesale; a clean package avoids a contentious rewrite, and rmc can adopt
   it later).
2. **Highlight under-ink scope**: is the underlay per-layer or page-global on
   device? (Does ink in layer 1 cover a highlight in layer 2?) Needs a
   two-layer calibration page.
3. **Legacy highlight color** (files without `color_rgba`): exact device
   pastel for yellow/green/pink — read from a calibration page.
4. **Ballpoint intensity**: adopt `pressure^5 + 0.7` (lines-are-rusty) vs
   maxio's speed-dependent formula vs fit our own curve — decide from
   calibration sweeps; also whether grain is dither (binary) or gray.
5. **Shader tool semantics**: does it intentionally accumulate where strokes
   overlap (unlike highlighter)? Calibration page needed.
6. **Pressure normalization**: RCU's `×0.005` is marked "guessed"; our
   formulas should be written against `pressure/255` and refit.
7. **Smoothing**: does xochitl interpolate between sampled points
   (Catmull-Rom-ish) or draw raw polylines? Compare close-ups at high zoom;
   affects outline-mode quality at low point density.
8. **skia-python version pin** and whether to also keep a no-skia fallback
   (pure-PIL stamp renderer) for environments where the wheel is unavailable.
9. **Paper Pro / color devices**: confirm coordinate conventions and
   `paper_size` handling with a real file.
10. **Performance target**: pages/sec for batch PDF export; decides whether
    stamp-mode needs numpy vectorization or Skia `drawAtlas`.
