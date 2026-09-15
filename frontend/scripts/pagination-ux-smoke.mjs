// Synthetic browser fixture with intercepted API pagination; never use live data.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdtempSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
const origin = "http://127.0.0.1:15173";
const archive = join(homedir(), ".cache/uv/archive-v0");
const modulePath = readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
const { chromium } = await import(pathToFileURL(modulePath).href);
const browser = await chromium.launch({ executablePath: join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
const output = mkdtempSync(join(tmpdir(), "waf-pagination-"));
const html = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><main id="fixture" style="max-width:1100px;margin:20px auto;padding:14px"></main><script type="module">
import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type; window.__vite_plugin_react_preamble_installed__=true;
await import('/src/styles.css'); const {default:React}=await import('/node_modules/.vite/deps/react.js'); const {default:ReactDOM}=await import('/node_modules/.vite/deps/react-dom_client.js'); const {default:Pagination}=await import('/src/Pagination.jsx'); const {TestRunHistory}=await import('/src/TestRuns.jsx'); const h=React.createElement;
function App(){const [offset,setOffset]=React.useState(1225),[limit,setLimit]=React.useState(25);return h('div',{},h('h1',{},'페이지 이동 검증'),h(Pagination,{label:'분석 페이지',total:2500,offset,limit,onOffsetChange:setOffset,onLimitChange:value=>{setLimit(value);setOffset(0);}}),h(TestRunHistory,{onSelect:()=>{}}));} ReactDOM.createRoot(document.getElementById('fixture')).render(h(App));</script></body></html>`;
try {
  for (const width of [1440, 390, 320]) {
    const context = await browser.newContext({ viewport: { width, height: 1000 }, serviceWorkers: "block" });
    const calls = [], violations = [], errors = [];
    await context.route("**/*", route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) { violations.push(url.origin); return route.abort(); }
      if (url.pathname === '/pagination-fixture') return route.fulfill({ contentType: 'text/html', body: html });
      if (!url.pathname.startsWith('/api/')) return route.continue();
      if (url.pathname !== '/api/v1/test-runs' || route.request().method() !== 'GET') { violations.push(url.pathname); return route.abort(); }
      calls.push(Object.fromEntries(url.searchParams));
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [], total: 125, limit: 10, offset: Number(url.searchParams.get('offset') || 0) }) });
    });
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin + '/pagination-fixture');
    if (width < 1000) await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
    const nav = page.getByRole('navigation', { name: '분석 페이지', exact: true });
    await nav.getByRole('button', { name: '50페이지', exact: true }).waitFor();
    assert.equal(await nav.locator('[aria-current="page"]').innerText(), '50');
    assert.equal(await nav.locator('.pagination-number').count(), 5);
    await nav.getByRole('button', { name: '마지막 페이지', exact: true }).click();
    assert.equal(await nav.locator('[aria-current="page"]').innerText(), '100');
    assert.equal(await nav.getByRole('button', { name: '다음 페이지', exact: true }).isDisabled(), true);
    await nav.getByRole('button', { name: '첫 페이지', exact: true }).click();
    assert.equal(await nav.locator('[aria-current="page"]').innerText(), '1');
    await nav.getByRole('button', { name: '3페이지', exact: true }).click();
    await nav.getByRole('button', { name: '다음 페이지', exact: true }).focus(); await page.keyboard.press('Enter');
    assert.equal(await nav.locator('[aria-current="page"]').innerText(), '4');
    await nav.getByRole('combobox').selectOption('50');
    assert.match(await nav.innerText(), /1 \/ 50페이지/);
    const history = page.getByRole('navigation', { name: '테스트 목록 페이지', exact: true });
    await history.getByRole('button', { name: '마지막 페이지', exact: true }).click();
    await history.locator('[aria-current="page"]').filter({ hasText: '13' }).waitFor();
    assert.equal(calls.at(-1).offset, '120'); assert.equal(calls.at(-1).limit, '10');
    assert.equal(calls.at(-1).reference_basis, 'latest');
    await history.getByRole('button', { name: '첫 페이지', exact: true }).click();
    await history.locator('[aria-current="page"]').filter({ hasText: /^1$/ }).waitFor();
    assert.equal(calls.at(-1).offset, '0');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    const centered = await nav.locator('.pagination-controls').evaluate(node => { const outer=node.parentElement.getBoundingClientRect(), inner=node.getBoundingClientRect(); return Math.abs((outer.left+outer.right)/2-(inner.left+inner.right)/2)<2; });
    assert.equal(centered, true); assert.deepEqual(violations, []); assert.deepEqual(errors, []);
    await page.screenshot({ path: join(output, 'pagination-' + width + '.png'), fullPage: true });
    await context.close();
  }
  console.log(JSON.stringify({ passed: 3, output, actualApiCalls: 0, llmCalls: 0 }));
} finally { await browser.close(); }
