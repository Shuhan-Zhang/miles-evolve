// Record the judge-facing UI for the demo video: node record_ui.js [url] [outdir]
// Uses hooks #make, #checks, #ledger. Produces <outdir>/*.webm (1920x1080) and timing.json.
const { chromium } = require('playwright');
(async () => {
  const url = process.argv[2] || 'http://localhost:8850', out = process.argv[3] || 'ui_rec';
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1,
    recordVideo: { dir: out, size: { width: 1920, height: 1080 } } });
  const page = await ctx.newPage();
  const t0 = Date.now(), marks = {};
  const mark = k => { marks[k] = (Date.now() - t0) / 1000; console.log(k, marks[k]); };
  await page.goto(url, { waitUntil: 'networkidle' }); mark('loaded');
  await page.waitForTimeout(3000);   // the gallery autoplays; let it settle
  const make = page.locator('#make');
  if (await make.count()) { await make.hover(); await page.waitForTimeout(600); await make.click(); mark('make_clicked'); }
  await page.locator('#checks').waitFor({ state: 'visible', timeout: 120000 }).catch(() => {});
  mark('checks_visible');
  await page.locator('#checks').scrollIntoViewIfNeeded().catch(() => {});
  await page.waitForTimeout(4000); mark('checks_held');
  const led = page.locator('#ledger');
  if (await led.count()) { await led.scrollIntoViewIfNeeded(); mark('ledger'); await page.waitForTimeout(5000); }
  await page.mouse.wheel(0, -4000); await page.waitForTimeout(2000); mark('end');
  await ctx.close(); await browser.close();
  require('fs').writeFileSync(out + '/timing.json', JSON.stringify(marks, null, 1));
})();
