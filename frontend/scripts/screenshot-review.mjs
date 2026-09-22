// Build a local review index and contact sheets from synthetic browser captures.
import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
const output = process.env.SCREENSHOT_DIR || fileURLToPath(new URL('../../test-results/responsive-ui/after', import.meta.url));
const rows = JSON.parse(readFileSync(join(output, 'audit.json'), 'utf8'));
const escape = text => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
const css = 'body{margin:0;padding:20px;background:#edf1f5;color:#172033;font:13px system-ui}h1{font-size:22px}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}article{padding:12px;background:white;border:1px solid #ccd4df;border-radius:10px;min-width:0}h2{font-size:12px;margin:0 0 10px}img{width:100%;height:430px;object-fit:contain;object-position:top}a{color:#26775e}';
writeFileSync(join(output, 'index.html'), `<!doctype html><html lang="ko"><meta charset="utf-8"><title>WAF UI review</title><style>${css}</style><h1>WAF UI review · ${rows.length} captures</h1><p>합성 API를 사용하는 실제 React 화면입니다. 이미지를 누르면 원본 전체 화면이 열립니다.</p><div class="grid">${rows.map(row => `<article><h2>${escape(row.name)} · ${row.width}px</h2><a href="${escape(row.name)}.png"><img loading="lazy" src="${escape(row.name)}.png" alt="${escape(row.name)}"></a></article>`).join('')}</div></html>`);
const archive = join(homedir(), '.cache/uv/archive-v0');
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, 'playwright/driver/package/index.mjs')).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), '.cache/ms-playwright/chromium-1208/chrome-linux64/chrome'), headless: true, args: ['--no-sandbox'] });
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } }); await page.route('**/*', r => r.abort());
  const review = rows.filter(row => /^(desktop|mobile)-/.test(row.name) && !/-(SK|Light|Dark)-/.test(row.name));
  for (let index = 0; index < review.length; index += 8) {
    const group = review.slice(index, index + 8);
    await page.setContent(`<html><style>${css}</style><div class="grid">${group.map(row => `<article><h2>${escape(row.name)}</h2><img src="data:image/png;base64,${readFileSync(join(output, row.name + '.png')).toString('base64')}"></article>`).join('')}</div></html>`);
    await page.screenshot({ path: join(output, 'review-sheet-' + (index / 8 + 1) + '.png'), fullPage: true });
  }
  console.log('Local index and ' + Math.ceil(review.length / 8) + ' review sheets created.');
} finally { await browser.close(); }
