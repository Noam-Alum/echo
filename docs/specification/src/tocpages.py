import re, json, subprocess, pathlib
d = pathlib.Path(__file__).parent
txt = subprocess.run(['pdftotext','-layout',str(d/'body.pdf'),'-'],capture_output=True,text=True).stdout
pages = txt.split('\f')
html = (d/'spec.html').read_text()
rows = re.findall(r'<li class="l[12]"><span class="tn">([\d.A-Z]+)</span><span class="tt">'
                  r'<a href="#([^"]+)">([^<]*)</a>', html)
out, missing = {}, []
for num, aid, title in rows:
    pat = re.compile(r'^\s*' + re.escape(num) + r'\s+' + re.escape(title.strip()) + r'\s*$', re.M)
    for i, pg in enumerate(pages, 1):
        if pat.search(pg):
            out[aid] = i; break
    else:
        missing.append((num, title))
(d/'tocpages.json').write_text(json.dumps(out, indent=1))
print(f'mapped {len(out)}/{len(rows)} headings')
if missing: print('  unmatched:', missing[:8])
