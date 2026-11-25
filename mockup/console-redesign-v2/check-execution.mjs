// Read-only browser checks for the offline execution-record wireframe.
// Fixtures below are synthetic; every external HTTP(S) request is blocked.
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
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), '.cache/ms-playwright/chromium-1208/chrome-linux64/chrome'), headless: true, args: ['--no-sandbox'] });
const output = mkdtempSync(join(tmpdir(), 'waf-execution-'));
const result = { output, checks: [], screenshots: [], errors: [], warnings: [], network: [] };
let page;
const tick = async () => {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.waitForTimeout(100);
};
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, reducedMotion: 'reduce', serviceWorkers: 'block' });
  await context.route(/^https?:/, route => { result.network.push(route.request().url()); return route.abort(); });
  page = await context.newPage(); page.setDefaultTimeout(8000);
  page.on('pageerror', error => result.errors.push(error.message));
  page.on('console', message => { if (['warning', 'error'].includes(message.type())) result.warnings.push(message.text().slice(0, 500)); });
  const target = process.env.WIREFRAME_SOURCE === '1' ? join(root, 'index.html') : join(root, '..', 'waf-console-redesign-v2.html');
  const base = pathToFileURL(target).href;
  const visit = async params => {
    await page.evaluate(params => WAF.go('analysis', params), params); await tick();
    assert.equal(await page.locator('.wireframe-error').count(), 0);
  };
  const capture = async name => {
    await page.mouse.move(1, 1);
    await page.evaluate(() => document.getElementById('toast')?.classList.remove('visible'));
    const path = join(output, `${name}.png`); await page.screenshot({ path, fullPage: true }); result.screenshots.push(path);
  };
  await page.goto(`${base}#analysis?id=ana-025&from=test&test=test-1&tab=execution`);
  await page.locator('#execution-steps [data-action="analysis-step"]').first().waitFor(); await tick();
  assert.deepEqual((await page.locator('.detail-tabs>.local-tab').allTextContents()).map(s => s.trim()), ['분석 결과', '실행', '보고서']);
  assert.equal(await page.locator('#execution-detail-pane').count(), 1);
  assert.deepEqual((await page.locator('[data-action="analysis-step-view"]').evaluateAll(elements => elements.map(el => el.dataset.view))).sort(), ['input', 'meta', 'output']);
  assert.ok(await page.locator('#execution-record-json').count());
  await capture('test-execution-default-1440');
  result.checks.push('Test execution has a selectable stage list and separate input/output/metadata JSON views.');

  const timing = await page.evaluate(() => {
    const complete = { status: 'completed', total_elapsed_ms: 10000, queue_wait_ms: 4000, processing_duration_ms: 6000 };
    const missing = { status: 'completed', duration: 99, created_at: '2026-11-25T03:00:00Z', completed_at: '2026-11-25T03:02:00Z' };
    const active = { status: 'processing', total_elapsed_ms: 90000, queue_wait_ms: 70000, processing_duration_ms: 20000 };
    const explicitZero = { status: 'completed', total_elapsed_ms: 0, queue_wait_ms: 0, processing_duration_ms: 0 };
    const invalid = { status: 'completed', total_elapsed_ms: -1, queue_wait_ms: '4000', processing_duration_ms: Number.NaN };
    return { complete: WAF.timing.values(complete), missing: WAF.timing.values(missing), invalid: WAF.timing.values(invalid), zero: WAF.timing.values(explicitZero), summary: WAF.timing.summary([complete, missing, active]), formats: [WAF.timing.format(null), WAF.timing.format(0)] };
  });
  for (const [key, expected] of [['total', 10], ['queue', 4], ['processing', 6]]) assert.equal(timing.complete[key], expected, `${key} milliseconds must become seconds.`);
  for (const key of ['total', 'queue', 'processing']) {
    assert.equal(timing.missing[key], null, `${key} absent data must not become zero.`);
    assert.equal(timing.invalid[key], null, `${key} invalid data must not become a measurement.`);
    assert.equal(timing.zero[key], 0, `${key} explicit measured zero remains meaningful.`);
    assert.equal(timing.summary.recorded[key], 1, `${key} mean denominator uses only recorded completed samples.`);
    assert.equal(timing.summary.mean[key], timing.complete[key]);
    assert.equal(timing.summary.p95[key], timing.complete[key]);
  }
  assert.equal(timing.summary.completed, 2);
  assert.equal(timing.summary.excluded, 1);
  assert.deepEqual(timing.formats, ['—', '0.0초']);
  assert.equal(timing.complete.total, timing.complete.queue + timing.complete.processing);
  result.checks.push('Total includes queue, processing excludes queue; absent measurements stay null and averages/p95 exclude missing or unfinished samples.');

  await page.evaluate(() => {
    const item = WAF.data.analyses.find(row => row.id === 'ana-025');
    item.created_at = '2026-11-25T03:00:00Z'; item.started_at = '2026-11-25T03:00:04Z'; item.completed_at = '2026-11-25T03:00:10Z';
    item.total_elapsed_ms = 10000; item.queue_wait_ms = 4000; item.processing_duration_ms = 6000;
    item.execution_steps = [
      { id: 'receive', name: '입력 확인', note: '합성 입력을 확인합니다.', status: 'completed', duration_ms: 50, input: { receive_input: true }, output: { accepted: true }, metadata: { source: 'synthetic' } },
      { id: 'primary', name: '1차 분석', note: '입력과 출력을 분리하는 합성 단계입니다.', status: 'completed', duration_ms: 2500,
        input: { input_unique: '입력 전용 <script>window.__executionInjected=true</script>', request: { payload: '<img src="https://never-request.invalid/input" onerror="window.__executionInjected=true">' } },
        output: { output_unique: '출력 전용 <img src="https://never-request.invalid/output" onerror="window.__executionInjected=true">', verdict: 'true_positive' },
        metadata: { metadata_unique: '기술 정보 전용', attempt_count: 1, usage: { input_tokens: 120, output_tokens: 45 } } },
      { id: 'verifier', name: '추가 검증', note: '실패 기록의 출력 부재를 확인합니다.', status: 'failed', duration_ms: 1000, input: { verifier_input: true }, output: null, metadata: { error_code: 'synthetic_output_incomplete' } },
      { id: 'editor', name: '근거 정리', note: '실행하지 않은 단계입니다.', status: 'skipped', duration_ms: null, metadata: { reason: '합성 검증용 건너뜀' } },
      { id: 'pending', name: '합성 대기 단계', note: '아직 시작하지 않은 단계입니다.', status: 'pending', duration_ms: null },
      { id: 'active', name: '합성 처리 단계', note: '처리 중인 단계입니다.', status: 'processing', duration_ms: null },
    ];
    window.__executionInjected = false;
    WAF.go('analysis', { id: item.id, from: 'test', test: 'test-1', tab: 'execution', step: 'primary', view: 'output' });
  });
  await tick();
  assert.match(page.url(), /step=primary/); assert.match(page.url(), /view=output/);
  assert.match(await page.locator('#execution-record-json').innerText(), /output_unique/);
  assert.doesNotMatch(await page.locator('#execution-record-json').innerText(), /input_unique|metadata_unique/);
  assert.equal(await page.locator('#execution-record-json img, #execution-record-json script').count(), 0);
  assert.equal(await page.evaluate(() => window.__executionInjected), false);
  await page.locator('[data-action="analysis-step-view"][data-view="input"]').click(); await tick();
  assert.match(page.url(), /view=input/);
  assert.match(await page.locator('#execution-record-json').innerText(), /input_unique/);
  assert.doesNotMatch(await page.locator('#execution-record-json').innerText(), /output_unique|metadata_unique/);
  await capture('test-execution-input-1440');
  for (const view of ['output', 'meta']) {
    await page.locator(`[data-action="analysis-step-view"][data-view="${view}"]`).focus();
    await page.keyboard.press('Enter'); await tick();
    assert.equal(await page.evaluate(() => document.activeElement?.dataset.action), 'analysis-step-view');
    assert.equal(await page.evaluate(() => document.activeElement?.dataset.view), view, 'Replacing the detail JSON must preserve keyboard focus on the selected record-view button.');
  }
  assert.match(await page.locator('#execution-record-json').innerText(), /metadata_unique/);
  assert.doesNotMatch(await page.locator('#execution-record-json').innerText(), /input_unique|output_unique/);
  await page.reload(); await tick();
  assert.match(page.url(), /id=ana-025/); assert.match(page.url(), /step=primary/); assert.match(page.url(), /view=meta/);
  // Fixture data is intentionally reset by a real reload; the link still points
  // to the equivalent default sample stage, rather than an unrelated page.
  assert.equal(await page.locator('#execution-detail-pane').count(), 1);
  result.checks.push('Input/output/technical JSON are separated and escaped; analysis, stage and view deep links survive reload.');

  // Reinstall an isolated fixture for failure/skip states after reload.
  await page.evaluate(() => {
    const item = WAF.data.analyses.find(row => row.id === 'ana-025');
    item.execution_steps = [
      { id: 'failed', name: '실패한 합성 단계', status: 'failed', duration_ms: 1000, input: { request_recorded: true }, output: null, metadata: { error_code: 'synthetic_failure' } },
      { id: 'skipped', name: '건너뛴 합성 단계', status: 'skipped', duration_ms: null },
      { id: 'pending', name: '대기 중 합성 단계', status: 'pending', duration_ms: null },
      { id: 'active', name: '처리 중 합성 단계', status: 'processing', duration_ms: null },
    ];
    WAF.go('analysis', { id: item.id, from: 'test', test: 'test-1', tab: 'execution', step: 'failed', view: 'output' });
  });
  await tick();
  assert.match(await page.locator('#execution-detail-pane').innerText(), /실패/);
  assert.doesNotMatch(await page.locator('#execution-detail-pane').innerText(), /"preserved":\s*true/);
  for (const step of ['skipped', 'pending', 'active']) {
    await page.locator(`#execution-steps [data-action="analysis-step"][data-step="${step}"]`).focus();
    await page.keyboard.press('Enter'); await tick(); assert.match(page.url(), new RegExp(`step=${step}`));
    assert.equal(await page.evaluate(() => document.activeElement?.dataset.step), step, 'Changing the stage must preserve focus on its stage selector.');
    const text = await page.locator('#execution-detail-pane').innerText();
    assert.doesNotMatch(text, /0(?:\.0)?초/, 'Unmeasured stage duration must not display measured zero.');
    assert.match(text, step === 'skipped' ? /건너|미실행|실행하지/ : step === 'pending' ? /대기/ : /처리 중|진행 중/);
  }
  result.checks.push('Failed, skipped, pending and processing steps show honest status and unmeasured durations; keyboard selection works.');

  await page.locator('[data-action="analysis-step"][data-step="result"]').click(); await tick();
  assert.match(page.url(), /step=result/);
  assert.match(await page.locator('#execution-detail-pane').innerText(), /최종|결과/);
  result.checks.push('Final result JSON is a separate selection, not a fabricated execution stage.');

  await visit({ id: 'ana-028', from: 'test', test: 'test-1', tab: 'execution', view: 'output' });
  for (const theme of ['light', 'dark', 'sk']) {
    await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
    await capture(`test-execution-${theme}-1440`);
  }
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 1050 }); await tick();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Execution must not overflow the body at ${width}px.`);
    await capture(`test-execution-${width}`);
  }
  await page.setViewportSize({ width: 1440, height: 900 }); await tick();
  assert.ok(await page.locator('#execution-workspace').evaluate(workspace => workspace.getBoundingClientRect().bottom <= innerHeight + 1), '900px-high execution workspace should fit without forcing the bottom off-screen.');
  await capture('test-execution-1440x900');
  await page.setViewportSize({ width: 1440, height: 1050 });
  await visit({ id: 'ana-007', tab: 'execution' });
  assert.deepEqual((await page.locator('.detail-tabs>.local-tab').allTextContents()).map(s => s.trim()), ['판정', '근거·입력', '실행', '보고서']);
  result.checks.push('Desktop/mobile and three themes render without body overflow; Production retains its four tabs.');
  await page.evaluate(() => document.documentElement.dataset.theme = 'light');
  for (const [route, name] of [['schema', 'schema-spacing'], ['agents', 'agents-spacing'], ['test?id=test-1&tab=statistics', 'statistics-spacing']]) {
    await page.evaluate(route => { location.hash = route; }, route); await tick();
    assert.equal(await page.locator('.wireframe-error').count(), 0);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${name} should keep table/form overflow inside its own container.`);
    await capture(name);
  }
  result.checks.push('Schema, Agent settings and test statistics render cleanly after the shared spacing changes.');
  assert.equal(result.errors.length, 0); assert.equal(result.network.length, 0);
  await context.close();
} catch (error) {
  result.errors.push(error.stack || error.message);
  if (page && !page.isClosed()) {
    result.debug = await page.evaluate(() => ({ url: location.href, body: document.body.innerText.slice(0, 6000), route: window.WAF?.route }));
    await page.screenshot({ path: join(output, 'failure.png'), fullPage: true }).catch(() => {});
  }
  throw error;
} finally {
  await browser.close(); writeFileSync(join(output, 'review.json'), JSON.stringify(result, null, 2)); console.log(JSON.stringify(result, null, 2));
}
