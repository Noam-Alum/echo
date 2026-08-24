import pathlib, sys, glob, re

def scope_svg(src, cls):
    """Inline <svg><style> is NOT scoped to the svg -- it applies to the whole
    document. Prefix every selector with a per-figure root class so figures
    cannot inherit each other's rules."""
    import re
    src = re.sub(r'<svg\b', f'<svg class="{cls}"', src, count=1)
    def fix(m):
        body = m.group(1)
        def rule(rm):
            sels = ','.join(f'.{cls} {s.strip()}' for s in rm.group(1).split(','))
            return sels + '{'
        return '<style>' + re.sub(r'([^{}]+)\{', rule, body) + '</style>'
    return re.sub(r'<style>(.*?)</style>', fix, src, flags=re.S)

d = pathlib.Path(__file__).parent
parts = [d/'00-head.html'] + sorted(p for p in d.glob('[0-9][0-9]-*.html') if not p.name.startswith('00'))
html = ''.join(p.read_text() for p in parts) + '\n</body></html>\n'

# inject diagrams: SVG_FOO -> svg-foo.svg
for svg in sorted(d.glob('svg-*.svg')):
    key = 'SVG_' + svg.stem[4:].upper().replace('-', '_')
    if key in html:
        html = html.replace(key, scope_svg(svg.read_text(), 'f-' + svg.stem[4:]))
    else:
        print(f'  warn: {svg.name} unused (looked for {key})')
missing = [w for w in set(__import__('re').findall(r'SVG_[A-Z_]+', html))]
if missing: print('  MISSING SVG:', missing)

# build TOC from h1/h2
rows = []
for m in re.finditer(r'<(h1|h2)[^>]*>(?:<span class="n">([^<]*)</span>)?(.*?)</\1>', html, re.S):
    tag, num, title = m.group(1), (m.group(2) or '').strip(), re.sub(r'<[^>]+>','',m.group(3)).strip()
    if not num or title in ('Table of Contents',): continue
    aid = 'sec-' + num.replace('.', '-')
    html = html.replace(m.group(0), m.group(0).replace('<'+tag, '<'+tag+' id="'+aid+'"', 1), 1)
    rows.append((tag, num, title, aid))
toc = []
for tag, num, title, aid in rows:
    cls = 'l1' if tag == 'h1' else 'l2'
    toc.append(f'<li class="{cls}"><span class="tn">{num}</span><span class="tt">'
               f'<a href="#{aid}">{title}</a></span><span class="dots"></span>'
               f'<span class="tp" data-ref="{aid}">&nbsp;</span></li>')
html = html.replace('TOC_PLACEHOLDER', '\n'.join(toc))

# second pass: inject page numbers discovered from a prior render
import json
pm = d/'tocpages.json'
if pm.exists():
    pages = json.loads(pm.read_text())
    hit = 0
    for aid, pg in pages.items():
        needle = f'<span class="tp" data-ref="{aid}">&nbsp;</span>'
        if needle in html:
            html = html.replace(needle, f'<span class="tp" data-ref="{aid}">{pg}</span>', 1)
            hit += 1
    print(f'  toc page numbers injected: {hit}/{len(rows)}')
(d/'spec.html').write_text(html)
print(f'built spec.html: {len(html)} chars, {len(rows)} toc entries, {len(parts)} parts')
