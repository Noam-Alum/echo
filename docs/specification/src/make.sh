#!/bin/zsh
set -e
cd "$(dirname "$0")"
OUT="../E.C.H.O-Platform-Specification.pdf"
python3 build.py
node render.js spec.html body.pdf
# resolve TOC page numbers, then rebuild until stable
for i in 1 2 3; do
  python3 tocpages.py
  cp -f tocpages.json .tocpages.prev 2>/dev/null || true
  python3 build.py
  node render.js spec.html body.pdf
  python3 tocpages.py
  if cmp -s tocpages.json .tocpages.prev; then echo "toc stable after pass $i"; break; fi
done
node render.js cover.html cover.pdf cover
pdfunite cover.pdf body.pdf "$OUT"
echo "--- $OUT ---"
pdfinfo "$OUT" | grep -E 'Pages|Page size|File size'
