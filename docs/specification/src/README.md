# Building the specification PDF

`./make.sh` produces `../E.C.H.O-Platform-Specification.pdf`.

## Pipeline

1. `build.py` concatenates `00-head.html` + the numbered section files + the appendices into
   `spec.html`, injects each `svg-*.svg` at its `SVG_<NAME>` placeholder, and builds the table of
   contents from the `h1`/`h2` headings.
2. `render.js` drives Chrome through `puppeteer-core` to produce `body.pdf`, with a running header and
   footer that Chrome's CLI `--print-to-pdf` cannot supply.
3. `tocpages.py` reads the rendered PDF, maps each heading to its page, and writes `tocpages.json`;
   `build.py` injects those numbers on the next pass. `make.sh` iterates until the mapping is stable.
4. The cover renders separately (no running header) and `pdfunite` joins it to the body, so the cover
   is unnumbered and body page 1 is the table of contents.

## Editing

- **Prose** lives in the numbered `NN-*.html` files. Requirements are
  `<div class="req"><span class="rid">R-nn</span>…</div>`; keep them numbered in document order.
- **Appendix B is generated** by the script embedded in the build history — it is derived from the
  `R-nn` blocks, so renumbering a requirement means regenerating it.
- **Diagrams** are hand-authored SVG in `svg-*.svg`, injected by filename:
  `svg-arch.svg` → `SVG_ARCH`.

## One trap worth knowing

Inline `<svg><style>` is **not scoped to the SVG** — its rules apply to the whole document. Figures
here share short class names (`.t`, `.s`, `.e`, `.hd`), so without scoping a later figure silently
inherits an earlier figure's rules. `build.py` rewrites every figure's selectors with a per-figure
root class (`.f-arch`, `.f-lifecycle`, …) at injection time. Do not remove that step; the symptom is
subtle, such as text anchored differently and running off the canvas.
