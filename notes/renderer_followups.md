# rmrender follow-up plans

Separate plans for each post-M4 follow-up, in priority order (template
backgrounds and multi-page export first, per jrk). Companion to
`renderer_plan.md` (status/history) and `renderer_research.md` (format
findings). Each plan stands alone and can be picked up independently.

---

## 1. Template background rendering

**Goal:** pages render over their template (lines, grid, dots, ...) the
way the device shows them, in all three backends.

**What the data provides:** each page's template name lives in the
notebook `.content` at `cPages.pages[i].template.value` (e.g. `"Blank"`,
`"P Lines small"`); `SceneInfo.background_visible` gates display. The
template *artwork* is NOT in the notebook — the device stores it under
`/usr/share/remarkable/templates/` as paired `.svg` + `.png` plus a
`templates.json` manifest. Those files are reMarkable's proprietary
assets: we must not bundle them; users supply a directory synced from
their device (same approach as RCU/rmc/remy).

**Approach:**
1. `templates.py`: `TemplateLibrary(dir)` resolving name → SVG/PNG path
   via `templates.json` (fall back to filename match). Missing template
   or `"Blank"` → plain white.
2. Drawing order becomes: template → highlights → ink. Highlights can no
   longer be plain opaque (they'd hide template lines): switch the
   highlight paint to `BlendMode.kDarken` (per-channel min). Over white
   this is identical to today's opaque union — same-color overlaps stay
   flat — and over template lines the darker line wins, matching the
   device. This single change covers raster and PDF (Skia maps kDarken
   into the PDF graphics state). See plan 3 for the SVG caveat.
3. Raster backend: prefer the template SVG via `skia.SVGDOM` if the
   installed skia-python exposes it (vector-crisp at any scale);
   otherwise decode the PNG and `drawImageRect` to the page.
4. PDF backend: same drawing call lands in the PDF (SVGDOM as vector
   ops, PNG as an embedded image — acceptable fallback).
5. SVG backend: inline the template SVG's content as a group, or embed
   the PNG base64 as `<image>` (fallback).
6. CLI: `--templates DIR` flag (and `RMRENDER_TEMPLATES` env var);
   warn-and-continue when a named template isn't found.

**Calibration:** one capture of a non-blank template page (ink +
highlight over template lines) — verifies the Darken assumption for
highlight-over-template and the template raster alignment. Add to the
golden suite with the template directory checked in ONLY if jrk's
license read allows committing device template files to a private fork;
otherwise keep captures local-only and gate the test on the directory's
presence.

**Open questions:** does the device dim template lines under highlights
(pure Darken) or fully repaint them? Worth eyeballing the capture
closely. Does `background_visible=False` occur in real exports?

---

## 2. Multi-page notebook export

**Goal:** `python -m rmrender NOTEBOOK_DIR out.pdf` produces one PDF for
the whole notebook; also per-page PNG/SVG batch export to a directory.

**Approach:**
1. New `notebook.py`: move `notebook_pages()` out of `calibrate.py`
   (calibrate imports it from here). Parse `.content` for page order
   (skip `"deleted"` entries), per-page template name, document
   `orientation`, `fileType`.
2. `render_notebook_pdf(notebook_dir, out_pdf, templates=None)`: one
   `skia.PDF.MakeDocument`, `beginPage`/`endPage` per page. Page size
   per page from each page's own `SceneInfo.paper_size` (don't assume
   uniform); landscape orientation = swap + rotate canvas. Reuse
   `render_scene(vector=True)` and plan 1's template drawing.
3. Batch raster/SVG: `render_notebook_pages(dir, out_dir, fmt, scale)`
   emitting `01.png`, `02.png`, ... (page numbers, not UUIDs).
4. CLI: detect a directory (or `.content` path) as input; infer mode
   from output extension (`.pdf` → single document; directory → batch).
5. **Phase 2 — PDF-backed notebooks** (`fileType: "pdf"`): underlay each
   base PDF page and draw annotations over it. Skia can't import PDF
   pages, so assemble with pymupdf instead: render each annotation page
   to a transparent-background overlay (raster at configurable dpi, or
   single-page PDF) and stamp it onto the base page (`show_pdf_page` /
   `insert_image`). Highlights must use multiply/darken here or they
   will hide the document text under them. `cPages.pages[i].redir`
   maps notebook pages to base-PDF page numbers.

**Tests:** golden the calibration notebook itself: 13-page PDF, assert
page count + rasterize 2-3 pages back through the metrics. Batch-export
smoke test.

**Open questions:** none blocking for native notebooks. Phase 2 needs a
small PDF-backed capture (base PDF + annotated pages + official export)
to verify alignment, `redir` handling, and highlight blending over text.

---

## 3. Highlight blending for non-white backgrounds (SVG)

**Goal:** plan 1's kDarken approach has one weak spot: Skia's SVG
backend writes blend modes as CSS `mix-blend-mode`, which some
consumers (notably cairosvg, many PDF converters) ignore — highlights
would cover template lines in those viewers.

**Approach (robust, viewer-independent):** pre-composite at generation
time instead of relying on viewer blending. Since rmrender draws the
template itself, it knows the geometry: emit template → highlight union
→ *template redrawn clipped to the highlight union* (`<clipPath>` from
the unioned highlight path; clip-paths are universally supported, unlike
blend modes) → ink. Highlight stays opaque; template lines reappear
inside it exactly where Darken would keep them.
- Prerequisite: per-color highlight outline union as a real path —
  build with skia pathops like `_stroke_outline` (also wanted by plan 5).
- Same trick is unnecessary for raster/PDF (kDarken works there); keep
  it SVG-only.

**Tests:** rasterize via cairosvg (which ignores mix-blend-mode — i.e.
the worst case) over a template and compare against the raster backend.

---

## 4. SVG file-size reduction

**Goal:** SVG pages are 85-600 KB (one element per segment, Skia's
verbose output). Target 5-10x smaller.

**Approach:** stop relying on Skia's SVG canvas for ink; hand-emit ink
geometry (keep the calibrated *models*, change the serializer):
1. Merge consecutive segments whose width quantizes to the same step
   (0.1 px) into one `<polyline>`; round coordinates to 2 decimals.
   Typical strokes have long constant-width runs (pressure saturates),
   so this collapses most elements.
2. Hoist shared attributes (`fill:none`, linecap, color) into `<g>`
   wrappers per stroke/pen.
3. Railroading segments: same run-merging on the rail/core polylines.
4. Shader/highlight union paths already exist (plans 3/5); emit with
   reduced precision.
5. Optional: gzip sibling output (`.svgz`).

**Tests:** existing SVG goldens must hold (rasterize-and-compare);
add a size budget assertion (e.g. ballpoint page < 60 KB).

**Note:** this naturally evolves the SVG backend into its own writer;
keep `render_scene`-via-SkSVGCanvas as the reference implementation to
diff against during the rewrite.

---

## 5. True PDF /Highlight annotations

**Goal:** exported PDFs where highlights are real, selectable PDF
annotations (extractable by other tools), not just painted ink — what
RCU does.

**Approach:** per (page, color): union the highlight stroke outlines
(pathops), decompose the union into rectangles/quads (cover with
per-stroke oriented quads from the original polylines — annotation
QuadPoints accept overlapping quads, so exact decomposition is
unnecessary), then post-process the Skia-produced PDF with pymupdf:
`page.add_highlight_annot(quads)` + `set_colors` + `update()`. Offer
`--annotate` to choose annotations instead of (or in addition to)
painted highlights.

**Open question:** annotation-only or annotation+paint by default?
(Viewers render annotation highlights themselves, usually multiplied —
double-painting would darken.) Recommend: `--annotate` replaces paint.

---

## 6. Typed text and text-anchored groups

**Goal:** render keyboard text (root text) and fix drawings anchored to
text lines (currently warned and pinned to page top).

**Approach:**
1. Port rmc's anchor-position logic: walk `TextDocument` paragraphs,
   accumulate per-style line heights (PLAIN 70, HEADING 150, ... — see
   rmc's `LINE_HEIGHTS`), record y per character CrdtId; feed
   `scene.py`'s `group_offset` from that map instead of warning.
2. Draw the text itself with Skia: `skia.Font` + `drawString` per line,
   bold/italic from `CrdtStr` properties, x from `pos_x`+anchor origin.
   Device font is proprietary; pick a metric-close open font and accept
   approximation (text fidelity is not stroke fidelity; document it).
3. Calibration: one capture of a page with typed text + drawings
   anchored mid-document (jrk types rarely — low priority but the
   anchor offsets matter even when the text itself isn't pixel-perfect,
   because anchored *drawings* shift with them).

---

## 7. Smart highlights (GlyphRange) for PDF-backed documents

**Goal:** text selected with the highlighter on PDFs ("snap" highlights)
renders/annotates from its stored rectangles.

**Approach:** `GlyphRange.rectangles` + `color_rgba` are already parsed
by rmscene; extend `scene.py` extraction to emit them as
highlight-class rectangle fills (raster/vector), and as native
`add_highlight_annot` quads in plan 5's annotation mode. Depends on
plan 2 phase 2 (PDF-backed pipeline) for end-to-end use; the extraction
piece can land independently with Wikipedia_highlighted test files
already in `tests/data/`.

---

## 8. Remaining pen polish (calibration-driven)

Small, independent, each driven by the existing harness:
- **Pencil grain character**: official redistributes ink (softer spread
  at same density; mae_blur plateaus ~33). Ideas: directional grain
  (elongate clumps along stroke direction), per-stamp jitter, or
  blue-noise instead of white-noise sprites. Time-boxed exploration.
- **Marker**: IoU 0.92 with structural shape error — likely needs flat
  caps + slight direction dependence (chisel tip) rather than width
  tweaks. Try `kButt_Cap` + perpendicular-offset rails similar to
  calligraphy behavior before inventing formulas.
- **Paintbrush envelope**: IoU 0.78; official strokes thin out more
  aggressively on fast/light passes. Revisit the pressure clamp with a
  speed term: `nib *= f(pressure) * g(speed)` fit from the capture.
- **Thick-stroke railroading**: official streak persists longer on
  thick strokes; try making the hole threshold nib-dependent
  (`RAILROAD_PRESSURE * (1 + k·nib)`), tune k against the highres
  screenshot.

---

## 9. Packaging & upstream

**Goal:** make rmrender usable outside this repo.

- Decide: publish as `rmrender` on PyPI (depends on `rmscene`,
  `skia-python`) vs. PR into rmc (replacing maxio-derived exporters).
  Standalone first; offer rmc integration once stable.
- Optional no-skia fallback (PIL-based raster for the solid pens only)
  if the skia wheel is unavailable on a target platform — decide based
  on actual demand; don't build speculatively.
- README section with CLI usage + fidelity table; example outputs.
- Carry the calibration suite as package test data or a git submodule?
  (13 PNG pages ≈ 1 MB — fine in-repo for now.)

---

## 10. Performance (only if it hurts)

Current: ~1.5 s/page raster at 1x (dominated by per-segment Python
loop + stipple stamping). For batch notebook export this is acceptable;
revisit only if real notebooks (100+ pages) feel slow. Levers, in
order: numpy-vectorize the arc-length walk; `canvas.drawAtlas` for
stamps (one call per stroke); reuse a single `skia.Surface` across
pages; multiprocessing per page (embarrassingly parallel).
