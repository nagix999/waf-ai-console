// Offline browser regression checks for the test-analysis split workspace.
// Synthetic data is installed in memory only. HTTP(S) requests are blocked.
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readdirSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const archive = join(homedir(), '.cache/uv/archive-v0');
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive)
  .map(name => join(archive, name, 'playwright/driver/package/index.mjs')).find(existsSync);
assert.ok(driver, 'A local Playwright driver is required.');
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH || join(homedir(), '.cache/ms-playwright/chromium-1208/chrome-linux64/chrome'),
  headless: true,
  args: ['--no-sandbox'],
});
const output = mkdtempSync(join(tmpdir(), 'waf-test-split-'));
const result = { output, checks: [], screenshots: [], errors: [], warnings: [], network: [] };
let page;
const tick = async page => {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.waitForTimeout(100);
};

try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, reducedMotion: 'reduce', serviceWorkers: 'block' });
  await context.route(/^https?:/, route => { result.network.push(route.request().url()); return route.abort(); });
  page = await context.newPage();
  page.setDefaultTimeout(8000);
  page.on('pageerror', error => result.errors.push(error.message));
  page.on('console', message => {
    if (['warning', 'error'].includes(message.type())) result.warnings.push(message.text().slice(0, 500));
  });
  const screenshot = async name => {
    await page.mouse.move(1, 1);
    await page.evaluate(() => document.getElementById('toast')?.classList.remove('visible'));
    const path = join(output, `${name}.png`);
    await page.screenshot({ path, fullPage: true });
    result.screenshots.push(path);
  };
  const target = process.env.WIREFRAME_SOURCE === '1' ? join(root, 'index.html') : join(root, '..', 'waf-console-redesign-v2.html');
  const base = pathToFileURL(target).href;
  await page.goto(`${base}#analysis?id=ana-025&from=test&test=test-1`);
  await page.locator('.test-analysis-workspace').waitFor();
  await tick(page);

  const tabs = await page.locator('#main-content .local-tab').allTextContents();
  assert.deepEqual(tabs.map(text => text.trim()), ['분석 결과', '실행', '보고서']);
  assert.equal(await page.locator('#test-results-scroll').count(), 1);
  assert.equal(await page.locator('#test-input-scroll').count(), 1);
  assert.ok(await page.locator('#test-input-fields.tabulator, #test-input-fields .tabulator').count());
  assert.ok(await page.evaluate(() => WAF.tables['test-input-fields'].getData().length > 0));
  result.checks.push('Test analysis combines verdict and evidence/input; the input fields use offline Tabulator.');
  await screenshot('test-split-default-ana025-1440');
  await page.evaluate(() => WAF.go('analysis', { id: 'ana-028', from: 'test', test: 'test-1' }));
  await tick(page);
  await page.locator('[data-action="analysis-source"]').first().evaluate(button => button.click());
  await tick(page);
  assert.ok(await page.locator('#source-highlight').count());
  await screenshot('test-split-default-ana028-selected-1440');
  await page.evaluate(() => WAF.go('analysis', { id: 'ana-025', from: 'test', test: 'test-1' }));
  for (const width of [1280, 1920]) {
    await page.setViewportSize({ width, height: 1050 });
    await tick(page);
    const fit = await page.evaluate(() => {
      const left = document.getElementById('test-results-scroll').getBoundingClientRect();
      const right = document.getElementById('test-input-scroll').getBoundingClientRect();
      return { noOverflow: document.documentElement.scrollWidth <= innerWidth + 1,
        sideBySide: right.left > left.right, equalHeight: Math.abs(left.height - right.height) < 1,
        viewportFit: left.bottom <= innerHeight + 1 && right.bottom <= innerHeight + 1 };
    });
    assert.ok(fit.noOverflow && fit.sideBySide && fit.equalHeight && fit.viewportFit, `${width}px desktop panes must fit the viewport.`);
    await screenshot(`test-split-default-ana025-${width}`);
  }
  await page.setViewportSize({ width: 1440, height: 1050 });
  await tick(page);
  result.checks.push('Default samples render cleanly; 1280px and 1920px desktop panes stay aligned inside the viewport.');

  // Deliberately includes HTML-like strings, repeated text, a metadata-only
  // excerpt, an incorrect stored span, and enough safe text to scroll both panes.
  const fixture = await page.evaluate(() => {
    const item = WAF.data.analyses.find(row => row.id === 'ana-025');
    const lines = Array.from({ length: 260 }, (_, i) => `X-Synthetic-${String(i).padStart(3, '0')}: safe fixture value ${i}`);
    lines[31] = 'X-Repeat: repeated-excerpt';
    lines[183] = 'X-Repeat: repeated-excerpt';
    lines[48] = 'X-Signature-Text: signature-only-source';
    lines[140] = 'X-First-Target: exact first source';
    lines[222] = 'X-Second-Target: exact second source';
    lines[250] = '<img src="https://never-request.invalid/image" onerror="window.__payloadExecuted=true">';
    lines[251] = '<script>window.__payloadExecuted=true</script>';
    const payload = `POST /synthetic/review HTTP/1.1\r\nHost: example.invalid\r\nX-Unicode: 한글 🙂\r\n${lines.join('\r\n')}\r\n\r\n{"fixture":true}`;
    const row = (id, excerpt, extra = {}) => ({ id, field: 'payload', supports: 'context', excerpt,
      interpretation: `합성 검증 문항 ${id}의 관측 내용과 해석입니다. ${'검토 결과를 원문과 대조해 확인합니다. '.repeat(5)}`,
      origins: [{ role: 'primary', index: id }],
      source_spans: [{ start: payload.indexOf(excerpt), end: payload.indexOf(excerpt) + excerpt.length }], ...extra });
    item.payload = payload;
    item.signature = 'signature-only-source';
    item.status = 'completed';
    item.evidence = [
      row('first', 'X-First-Target: exact first source', { supports: 'attack_request' }),
      row('second', 'X-Second-Target: exact second source', { supports: 'attack_request' }),
      row('metadata', 'signature-only-source', { field: 'signature', source_spans: [] }),
      row('ambiguous', 'repeated-excerpt', { source_spans: [] }),
      row('mismatch', 'X-First-Target: exact first source', { source_spans: [{ start: 4, end: 12 }] }),
      row('multiple', `${lines[160]}\n${lines[162]}`, { source_spans: [160, 162].map(index => ({ start: payload.indexOf(lines[index]), end: payload.indexOf(lines[index]) + lines[index].length })) }),
      ...Array.from({ length: 24 }, (_, i) => row(`extra-${i}`, lines[70 + i])),
    ];
    window.__payloadExecuted = false;
    WAF.refresh();
    return { payload, first: 'X-First-Target: exact first source', second: 'X-Second-Target: exact second source' };
  });
  await tick(page);
  assert.equal(await page.locator('#test-payload').textContent(), fixture.payload);
  assert.equal(await page.evaluate(() => window.__payloadExecuted), false);
  assert.equal(await page.locator('#test-payload img, #test-payload script').count(), 0);
  result.checks.push('Raw payload text, CRLF, HTML-like input and synthetic source offsets remain safe and unchanged.');

  const scrollState = () => page.evaluate(() => ({
    left: document.getElementById('test-results-scroll').scrollTop,
    right: document.getElementById('test-input-scroll').scrollTop,
    window: window.scrollY,
    leftOverflow: document.getElementById('test-results-scroll').scrollHeight > document.getElementById('test-results-scroll').clientHeight,
    rightOverflow: document.getElementById('test-input-scroll').scrollHeight > document.getElementById('test-input-scroll').clientHeight,
  }));
  await page.evaluate(() => {
    document.getElementById('test-results-scroll').scrollTop = 100;
    document.getElementById('test-input-scroll').scrollTop = 80;
  });
  const initialScroll = await scrollState();
  assert.ok(initialScroll.leftOverflow && initialScroll.rightOverflow, 'Both long panes must scroll internally.');
  const wheelInside = async id => {
    const box = await page.locator(id).boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height * 0.72);
    await page.mouse.wheel(0, 240);
    await tick(page);
  };
  await wheelInside('#test-results-scroll');
  const afterLeftWheel = await scrollState();
  assert.ok(afterLeftWheel.left > initialScroll.left);
  assert.equal(afterLeftWheel.right, initialScroll.right, 'Wheel inside evidence must not move input.');
  assert.equal(afterLeftWheel.window, initialScroll.window, 'Wheel inside evidence must not move body.');
  await wheelInside('#test-input-scroll');
  const before = await scrollState();
  assert.ok(before.right > afterLeftWheel.right);
  assert.equal(before.left, afterLeftWheel.left, 'Wheel inside input must not move evidence.');
  assert.equal(before.window, afterLeftWheel.window, 'Wheel inside input must not move body.');
  result.checks.push('Real mouse-wheel input scrolls only the hovered left/right pane, never its sibling or body.');
  await page.locator('[data-action="analysis-source"][data-evidence="1"]').evaluate(button => button.click());
  await tick(page);
  const after = await scrollState();
  assert.equal(after.left, before.left, 'Selecting a source must not reset the evidence scroll.');
  assert.equal(after.window, before.window, 'Selecting a source must not move the body.');
  assert.notEqual(after.right, before.right, 'The input pane should move to the cited source.');
  assert.equal(await page.locator('#source-highlight').textContent(), fixture.first);
  assert.equal(await page.locator('#test-payload').textContent(), fixture.payload, 'Highlighting must preserve the raw CRLF and Unicode text.');
  assert.equal(await page.locator('[data-test-evidence="1"].test-evidence-selected').count(), 1);
  assert.equal(await page.locator('#source-highlight').evaluate(mark => {
    const target = mark.getBoundingClientRect(), pane = document.getElementById('test-input-scroll').getBoundingClientRect();
    return target.top >= pane.top && target.bottom <= pane.bottom;
  }), true, 'The actual highlighted source should be visible inside the right pane.');
  assert.match(page.url(), /evidence=1/);
  assert.match(page.url(), /tab=result/);
  result.checks.push('Source selection keeps evidence/body scroll, moves only input, highlights the exact span and updates URL.');

  await page.locator('[data-action="analysis-source"][data-evidence="2"]').evaluate(button => button.focus({ preventScroll: true }));
  const beforeKeyboard = await scrollState();
  await page.keyboard.press('Enter');
  await tick(page);
  assert.equal(await page.locator('#source-highlight').textContent(), fixture.second);
  assert.equal((await scrollState()).left, beforeKeyboard.left);
  result.checks.push('Keyboard Enter on an evidence-source button uses the same precise source selection.');

  await page.locator('[data-test-evidence="1"] .evidence-interpretation').evaluate(element => element.click());
  await tick(page);
  assert.equal(await page.locator('#source-highlight').textContent(), fixture.first);
  result.checks.push('Clicking noninteractive evidence-card content selects the source.');

  await page.locator('[data-action="analysis-source"][data-evidence="3"]').evaluate(button => button.click());
  await tick(page);
  assert.equal(await page.locator('#source-highlight').count(), 0, 'A signature excerpt must not select matching payload text.');
  assert.equal(await page.locator('.test-input-field-selected').count(), 1);
  assert.match(await page.locator('.test-input-field-selected').innerText(), /signature/);
  assert.ok((await page.locator('#test-source-status').innerText()).trim());
  result.checks.push('Non-payload evidence selects its input-field row instead of an unrelated payload match.');

  for (const index of [4, 5]) {
    await page.locator(`[data-action="analysis-source"][data-evidence="${index}"]`).evaluate(button => button.click());
    await tick(page);
    assert.equal(await page.locator('#source-highlight').count(), 0, `Evidence ${index} must not guess a source position.`);
    assert.ok((await page.locator('#test-source-status').innerText()).trim(), 'Unlocatable evidence must have a visible explanation.');
    assert.equal(await page.locator('#test-source-status').evaluate(status => {
      const target = status.getBoundingClientRect(), pane = document.getElementById('test-input-scroll').getBoundingClientRect();
      return target.top >= pane.top && target.bottom <= pane.bottom;
    }), true, 'The right pane must bring the source-location explanation into view.');
  }
  result.checks.push('Repeated excerpts without a unique position and invalid stored spans are not arbitrarily highlighted.');

  await page.locator('[data-action="analysis-source"][data-evidence="6"]').evaluate(button => button.click());
  await tick(page);
  assert.equal(await page.locator('#test-payload [data-source-highlight]').count(), 2);
  assert.equal(await page.locator('#test-payload').textContent(), fixture.payload);
  result.checks.push('A linked excerpt with two exact stored spans highlights both original positions without merging or modifying source text.');

  await page.evaluate(() => WAF.go('analysis', { id: 'ana-025', from: 'test', test: 'test-1', tab: 'evidence', evidence: 1 }));
  await tick(page);
  assert.equal(await page.locator('.test-analysis-workspace').count(), 1);
  assert.equal(await page.locator('#main-content .local-tab.active').innerText(), '분석 결과');
  assert.equal(await page.locator('#source-highlight').textContent(), fixture.first);
  result.checks.push('Legacy Test evidence-tab links open the combined result workspace with source selection.');

  for (const theme of ['light', 'dark', 'sk']) {
    await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
    await screenshot(`test-split-${theme}-1440`);
  }
  await page.setViewportSize({ width: 390, height: 1050 });
  await tick(page);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Stacked mobile workspace must not overflow the page horizontally.');
  const mobile = await page.evaluate(() => {
    const left = document.getElementById('test-results-scroll').getBoundingClientRect();
    const right = document.getElementById('test-input-scroll').getBoundingClientRect();
    return { stacked: right.top >= left.bottom - 1, leftHeight: left.height, rightHeight: right.height };
  });
  assert.ok(mobile.stacked && mobile.leftHeight > 100 && mobile.rightHeight > 100, 'Narrow screens stack two usable panes.');
  await screenshot('test-split-mobile-390');
  result.checks.push('Mobile panes stack without body-level horizontal overflow.');

  await page.evaluate(() => window.scrollTo(0, 500));
  await page.setViewportSize({ width: 1440, height: 1050 });
  await tick(page);
  assert.ok(await page.locator('#test-analysis-workspace').evaluate(workspace => {
    const rect = workspace.getBoundingClientRect();
    return rect.height > 300 && rect.bottom <= innerHeight + 1;
  }), 'Restoring desktop width after mobile body scroll must recalculate a viewport-fitted workspace.');
  result.checks.push('Desktop workspace height remains correct after a scrolled mobile layout is widened.');
  await page.evaluate(() => WAF.go('analysis', { id: 'ana-007' }));
  await tick(page);
  assert.deepEqual((await page.locator('#main-content .local-tab').allTextContents()).map(text => text.trim()), ['판정', '근거·입력', '실행', '보고서']);
  assert.equal(await page.locator('.test-analysis-workspace').count(), 0);
  result.checks.push('Production analysis retains its existing four-tab layout.');
  assert.equal(result.errors.length, 0, 'There must be no browser exceptions.');
  assert.equal(result.network.length, 0, 'The standalone wireframe must not request external URLs.');
  await context.close();
} catch (error) {
  if (page && !page.isClosed()) {
    result.debug = await page.evaluate(() => ({ url: location.href, body: document.body.innerText.slice(0, 4000), waf: typeof WAF, route: window.WAF?.route }));
    await page.screenshot({ path: join(output, 'failure.png'), fullPage: true }).catch(() => {});
  }
  result.errors.push(error.stack || error.message);
  throw error;
} finally {
  await browser.close();
  writeFileSync(join(output, 'review.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
}
