# reMarkable v6 stroke rendering — research notes

Research conducted June 2026 against this repo (rmscene), rmc git HEAD, and six
third-party renderer codebases. Test artifacts: `tests/data/jrk_test.rm` with
official device renders `jrk_test.png` / `jrk_test_high_res.png` (on the
`renderer` branch).

## 1. What the v6 format captures (rmscene)

Each stroke is a `scene_items.Line`:

| Field | Meaning |
|---|---|
| `tool` | Pen id (ballpoint, fineliner, calligraphy, highlighter, shader, ...) |
| `color` | Palette index; `PenColor.HIGHLIGHT` (9) for all highlighter colors |
| `color_rgba` | Explicit RGBA for newer per-color highlights (e.g. `(190, 234, 254, 255)` for blue) |
| `thickness_scale` | UI brush-size setting (observed: 1.0, 2.0, 3.0; pre-v6 era used 1.875/2.0/2.125) |
| `starting_length` | Cumulative arc length carried over when a stroke continues an earlier one — texture phase continuity |
| `points` | List of `Point` |

Each `Point` (`scene_stream.py:360-378`):

| Field | Units / encoding |
|---|---|
| `x, y` | Screen pixels (1404×1872 @ 226 dpi), x centered on 0 (i.e. offset by 1404/2) |
| `speed` | v1: float×4; v2: uint16. Used by paintbrush/ballpoint intensity models |
| `direction` | Stroke azimuth, uint8: 0–255 = 0–2π |
| `width` | **Quarter-pixels**: `width / 4` = rendered nib width in screen px |
| `pressure` | 0–255 (saturates at 255 readily; weaker signal than `width`) |

### The critical fact about `width`

`width` is **not raw stylus input** — it is the *output* of xochitl's brush
dynamics model (pressure/tilt/speed → nib width), computed at capture time and
stored per point. The device bakes its rendering geometry into the file.

Empirical evidence from `jrk_test.rm` and the repo's test files:

- Highlighter: constant `width=120` → 30 px — the exact official nib width.
- Ballpoint (thickness 2.0/3.0): 12–27 → 3–6.8 px, tracking pressure; a single
  stroke shows a clean taper ramp `12,12,…,13,15,16,17,18,…` against pressure
  ramp `7→255`. Start/end tapers visible in the official render are **already
  in the data**.
- Calligraphy ("fountain pen"): 8–40 → 2–10 px — the direction-dependent nib
  is fully encoded; no nib simulation needed.
- Shader: 19–88 quarter-px, wide variation.

**Conclusion: a faithful renderer takes geometry from stored `width` and uses
`pressure`/`speed`/`direction` only for intensity/texture effects.**

## 2. Current renderer (rmc) and its failure modes

rmc (`src/rmc/exporters/svg.py`, `writing_tools.py`; heuristics inherited from
maxio, reverse-engineered in the *pre-v6* era):

- Chops strokes into chunks of `segment_length` points (ballpoint 5, pencil 2,
  fineliner/highlighter 1000); one `<polyline>` with a single constant
  `stroke-width` per chunk.
- Recomputes width from pressure/speed heuristics, largely ignoring stored
  `width` (fineliner: constant; highlighter: hardcoded 15 px = **half** the
  correct 30 px).
- Highlighter/pencil opacity set per element → alpha accumulates where strokes
  (or chunks) overlap; translucent pens get dark dots at chunk joints.
- Calligraphy heuristic produces blobby, dropout-prone output.
- `starting_length`, textures: unused.

**Released rmc 0.3.0 additionally crashes** on modern highlighter strokes:
`Pen.__init__` does `RM_PALETTE[color_id]` and `PenColor.HIGHLIGHT`(9) is
absent → `KeyError: 9`. The SVG is written incrementally, so the output is
truncated at the first highlighter stroke and every stroke after it in
document order is silently missing (observed: highlighter at index 19,
calligraphy strokes at 89–100 → "highlighter and fountain pen don't render").
Fixed in rmc git HEAD (`lookup_pen_color` + `color_rgba` support), unreleased.

## 3. Codebase survey — what each renderer does

### RCU — reMarkable Connection Utility (Davis Remmel, AGPL)
Mirror: github.com/404Wolf/remarkable-connection-utility. The most
device-faithful third-party renderer; supports v6 (bundles rmscene).

v6 adapter (`src/model/lines.py`, `readLines6`) — exact constants:

```python
seg = Segment(x = (p.x + 1404/2) * res_mod,
              y = p.y * res_mod,
              speed = p.speed,
              direction = p.direction,
              width = p.width / 4 * res_mod,      # stored width, quarter-px
              pressure = p.pressure * 0.005)      # comment: "guessed"
stroke = Stroke(..., width = 30 * res_mod, ...)   # per-stroke width for v6
```

Pens (`src/model/pens/`), drawn one Qt line per point pair:

- **Generic / Fineliner / Calligraphy**: `setWidthF(segment.width)` — stored
  width, verbatim. Round cap, miter join.
- **Ballpoint**: `width + (pressure − 1) · width/2`.
- **Marker**: `width · 0.7`.
- **Pencil (tilt)**: primary pass `width · 0.58` (= w − 0.42·w); **second
  "spatter" pass at 1.25× width behind**, texture at `0.7·pressure`. Raster
  mode: grain texture stamp selected by `index = 0.25 · (pressure·N)^1.21`
  (log bank); vector mode: lighten color by pressure, PDF graphics state
  `/BM /Darken`.
- **Mechanical pencil**: `width / 1.5`; linear texture bank quantized by
  pressure thresholds (0.10…0.90).
- **Paintbrush**: `0.75·width + (pressure−1) · 0.5625·width`; intensity
  `pressure · (2 − speed/75)` ("really fast movements produce really light
  strokes"); texture rotated to `direction + 90°`; vector `/BM /Darken`.
- **Highlighter**: constant `stroke.width` (30 for v6), flat cap, bevel join,
  `setColor` **forces alpha to 1.0** with comment: *"Since 2.11, reMarkable no
  longer shows overlapping highlights with transparency (the color is always
  absolute)."* Painted with `QPainter.CompositionMode_Multiply`; PDF graphics
  state `/BM /Multiply` (bitmap and vector). Also emits true PDF `/Highlight`
  annotations from `QPainterPathStroker` outline paths.
- Texture assets: `pens/pencil_textures_linear/*.ppm`,
  `pens/pencil_textures_log/*.ppm` (~100 files keyed 0.00–0.99),
  `pens/paintbrush_textures_log/*.ppm`.

### rmrl (rschroll, GPL3) — github.com/rschroll/rmrl
Direct Python/ReportLab port of RCU's pens (v5 only; no v6). Same constants as
RCU. Ships the same PPM texture banks. Highlighter: `(1.0, 0.914, 0.290)` at
alpha 0.392 (pre-2.11 behavior), square cap, one path per stroke.

### remy (bordaigorl) — github.com/bordaigorl/remy
Qt-based viewer/exporter (pre-v6). Independent confirmations:

- Width functions: default = stored width (`dynamic_width`); ballpoint =
  `round(width)`; pencil = `0.55·width`; mech pencil = `width/1.5`; a
  `very_dynamic_width = 0.7w + 0.3w·pressure` variant exists.
- **Highlighter: `QPainter.CompositionMode_Darken` + `setZValue(-1)`** — i.e.
  opaque pastel, per-channel-min blend, drawn *below* ink. Darken gives union
  of overlapping highlights for free and never tints ink.
- Pencil textures **generated procedurally**: N=15 random-dot QImages,
  dot count `∝ size²·(i+1)/N/2.5`, used as Qt brush at scale 0.4; fuzzy-edge
  second pass at `1.15× width` with texture at `0.7·pressure` (the spatter
  idea again).
- Accurate eraser: build stroked outline via `QPainterPathStroker` (width =
  stroke width) and use it as a **clip path** on previously drawn items.
- Erase-area strokes (pen 8): "The remarkable renderer seems to ignore
  these!" — skip them.
- Optional smoothing: Catmull-Rom/Bezier interpolation + Ramer–Douglas–Peucker
  simplification for fineliner/ballpoint.

### lines-are-rusty (ax3l) — github.com/ax3l/lines-are-rusty
Rust, pre-v6, SVG. Variable-width strokes emitted as one tiny two-point
`<path>` per point pair with `stroke-width = point.width`. **Ballpoint
intensity: `stroke-opacity = pressure^5 + 0.7`** — sharp falloff that only
lightens genuinely light touches (matches grain at stroke tails, where
pressure is low and speed high).

### maxio / rmc lineage (lschwetlick/maxio → chemag → rmc)
Source of rmc's heuristics (see §2). Useful only for its ballpoint intensity
model: `intensity = −0.1·(speed/4)/35 + 1.2·pressure/255 + 0.5`, clamped,
mapped to gray ≤ RGB 60.

### remarks (lucasrla) — github.com/lucasrla/remarks
v6 via rmscene. Converts highlighter strokes and `GlyphRange` smart highlights
to true PDF highlight annotations via PyMuPDF quads. Shapely used only for
bounding boxes (not union).

### ddvk/reader — github.com/ddvk/reader
Go, v6 parser only (the format reference rmscene credits). No renderer.

### Not analyzed
drawj2d (Java, SourceForge) — reportedly good fidelity, left for follow-up.
xochitl itself is closed-source; its brush textures/dynamics are only
observable via output.

## 4. Answers to specific rendering questions

### Moved strokes (`move_id`) — resolved, no renderer work needed
When strokes are moved (lasso select + move), xochitl tombstones the original
items and writes **brand-new Line items with rewritten point coordinates**;
`move_id` on the new item is only a provenance back-reference to the replaced
original (presumably for sync/undo), *not* a transform to apply at render
time. Verified by diffing `tests/data/Lines_v2.rm` against
`Lines_v2_updated.rm`: each updated line is an exact translated copy
(uniform dx=464.0, dy=−267.594 across every point) with width/pressure
preserved, new CrdtId, and `move_id` = the old line's CrdtId. A renderer
that simply walks the visible tree renders moves correctly for free.
(Open: whether *scaling* a selection also rescales stored nib widths —
same mechanism, needs one calibration capture.)

### Ballpoint grain at light pressure / high speed
Real device behavior. Two open-source models: lines-are-rusty's
`opacity = pressure^5 + 0.7` and maxio/rmc's speed+pressure intensity → gray.
E-ink has no alpha, so the native look is intensity-modulated *dithering*;
for bitmap output, noise-modulated stamping driven by such an intensity term
reproduces the grain better than uniform transparency.

### Highlighter semantics (device ≥ 2.11)
- Color is **absolute** (the stored pastel RGBA is the final pixel color),
  not a transparent layer.
- Rendered as an **under-layer** relative to ink; overlapping strokes form a
  flat union, never accumulating.
- Correct reproductions: (a) opaque pastel drawn before ink (union free on
  blank pages); (b) Darken (per-channel min) or Multiply blend for rendering
  over PDF backgrounds — Darken is preferable because pastel-on-pastel stays
  pastel regardless of draw order; (c) geometric union of stroke outlines
  into a single filled path (robust in any viewer, smaller files, doubles as
  PDF annotation geometry).

### Width semantics summary (for the renderer)
`final_nib_px = point.width / 4`. Per-pen multipliers from RCU (§3) apply on
top for pencil/marker/mech-pencil/paintbrush. Highlighter is constant 30 px
(and the file's stored point width agrees: 120/4 = 30).

## 5. Licensing notes

- rmscene/rmc: MIT. RCU: AGPL. rmrl: GPL3. remy: GPL3. lines-are-rusty: MIT.
- Numeric constants and formulas are facts and can be reused. **Do not copy
  RCU/rmrl texture PPMs or code verbatim into MIT-licensed code.** Generate
  textures procedurally (remy's approach) or sample our own from device
  output.

## 6. Reference renders for validation

- `tests/data/jrk_test.rm` + `jrk_test.png` + `jrk_test_high_res.png`
  (official device renders) — ballpoint × 2 sizes, calligraphy, highlighter.
- Repo test files with stroke variety: `Color_and_tool_v3.14.4.rm`
  (shader, ballpoint, colors), `More_color_highlight_shader_v3.15.4.2.rm`
  (highlighter, shader, full color palette).
- Gap: no official reference renders yet for pencil, mechanical pencil,
  paintbrush, marker, fineliner, shader — need calibration pages.
