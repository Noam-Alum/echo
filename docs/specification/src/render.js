const puppeteer = require('puppeteer-core');
const path = require('path');
(async () => {
  const [src, out, mode] = process.argv.slice(2);
  const browser = await puppeteer.launch({
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: 'new', args: ['--no-sandbox','--allow-file-access-from-files'],
  });
  const page = await browser.newPage();
  await page.goto('file://' + path.resolve(src), { waitUntil: 'networkidle0' });
  const chrome = mode === 'cover' ? {} : {
    displayHeaderFooter: true,
    headerTemplate: `<div style="font-family:-apple-system,sans-serif;font-size:7pt;color:#9aa4b2;
        width:100%;padding:0 18mm;display:flex;justify-content:space-between;">
        <span>E.C.H.O &mdash; Platform Specification</span><span>ECHO-SPEC-001 &middot; v1.0</span></div>`,
    footerTemplate: `<div style="font-family:-apple-system,sans-serif;font-size:7pt;color:#9aa4b2;
        width:100%;padding:0 18mm;display:flex;justify-content:space-between;">
        <span>24 August 2026</span><span class="pageNumber"></span></div>`,
  };
  await page.pdf({ path: out, format: 'A4', printBackground: true, ...chrome,
    margin: { top: mode==='cover' ? '0mm' : '17mm', bottom: mode==='cover' ? '0mm' : '15mm',
              left: '18mm', right: '18mm' } });
  await browser.close();
})();
