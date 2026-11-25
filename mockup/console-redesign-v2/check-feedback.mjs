// Offline-only feedback aggregation checks. All fixtures are synthetic and
// live only in an isolated browser context; no server writes or model calls.
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readdirSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
const root = dirname(fileURLToPath(import.meta.url));
const archive = join(homedir(), '.cache/uv/archive-v0');
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, 'playwright/driver/package/index.mjs')).find(existsSync);
assert.ok(driver, 'A local Playwright driver is required.');
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), '.cache/ms-playwright/chromium-1208/chrome-linux64/chrome'), headless: true, args: ['--no-sandbox'] });
const output = mkdtempSync(join(tmpdir(), 'waf-feedback-'));
const result = { output, checks: [], screenshots: [], errors: [], warnings: [], network: [] };
let page;
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, reducedMotion: 'reduce', serviceWorkers: 'block' });
  await context.route(/^https?:/, route => { result.network.push(route.request().url()); return route.abort(); });
  page = await context.newPage(); page.setDefaultTimeout(8000);
  page.on('pageerror', error => result.errors.push(error.message));
  page.on('console', message => { if (['warning', 'error'].includes(message.type())) result.warnings.push(message.text().slice(0, 500)); });
  const tick = async () => { await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))); await page.waitForTimeout(100); };
  const capture = async name => { await page.mouse.move(1, 1); await page.evaluate(() => document.getElementById('toast')?.classList.remove('visible')); const path = join(output, `${name}.png`); await page.screenshot({ path, fullPage: true }); result.screenshots.push(path); };
  const visit = async (route, params = {}) => { await page.evaluate(({ route, params }) => WAF.go(route, params), { route, params }); await tick(); assert.equal(await page.locator('.wireframe-error').count(), 0); };
  const ids = async () => (await page.evaluate(() => WAF.tables['feedback-table'].getData().map(row => row.id))).sort();
  const apply = async () => { await page.locator('#feedback-filters button[type="submit"]').click(); await tick(); };
  const file = process.env.WIREFRAME_SOURCE === '1' ? join(root, 'index.html') : join(root, '..', 'waf-console-redesign-v2.html');
  await page.goto(pathToFileURL(file).href + '#feedback');
  await page.locator('#feedback-table.tabulator').waitFor(); await tick();
  assert.equal(await page.locator('#feedback-filters [name="purpose"]').inputValue(), 'production');
  assert.ok(await page.evaluate(() => WAF.tables['feedback-table'].getData().every(row => row.purpose === 'production')));
  assert.equal(await page.locator('#feedback-category-chart [data-action="feedback-category"]').count(), 6);
  result.checks.push('Feedback opens in Production scope with real offline Tabulator and six category filters.');
  for (const theme of ['light', 'dark', 'sk']) { await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme); await capture(`feedback-${theme}-1440`); }
  for (const width of [1280, 390]) { await page.setViewportSize({ width, height: 1050 }); await tick(); assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)); await capture(`feedback-${width}`); }
  await page.setViewportSize({ width: 1440, height: 1050 }); await page.evaluate(() => document.documentElement.dataset.theme = 'light');

  await page.locator('#feedback-filters [name="start"]').fill('2026-11-01');
  await page.locator('#feedback-filters [name="end"]').fill('2026-11-25');
  await page.locator('#feedback-filters [name="version"]').selectOption('prod-config-1');
  await page.locator('#feedback-filters [name="category"]').selectOption('insufficient_evidence');
  await page.locator('#feedback-filters [name="query"]').fill('분석가'); await apply();
  for (const fragment of ['start=2026-11-01', 'end=2026-11-25', 'purpose=production', 'version=prod-config-1', 'category=insufficient_evidence']) assert.ok(page.url().includes(fragment));
  assert.ok(new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('q') === '분석가');
  await page.reload(); await tick();
  for (const [field, value] of [['start', '2026-11-01'], ['end', '2026-11-25'], ['purpose', 'production'], ['version', 'prod-config-1'], ['category', 'insufficient_evidence'], ['query', '분석가']]) assert.equal(await page.locator(`#feedback-filters [name="${field}"]`).inputValue(), value);
  await page.locator('[data-action="feedback-clear"]').click(); await tick();
  await page.locator('[data-action="feedback-range"][data-days="7"]').click(); await tick();
  assert.equal(await page.locator('#feedback-filters [name="start"]').inputValue(), '2026-11-19');
  assert.equal(await page.locator('#feedback-filters [name="end"]').inputValue(), '2026-11-25');
  await page.locator('[data-action="feedback-range"][data-days="30"]').click(); await tick();
  assert.equal(await page.locator('#feedback-filters [name="start"]').inputValue(), '2026-10-27');
  result.checks.push('Date/purpose/version/category/search apply and reload; 7-day and 30-day shortcuts use inclusive calendar dates.');

  const pure = await page.evaluate(() => {
    const analyses = [{ id: 'qa-pa', purpose: 'production', production_version_id: 'qa-v1', company: '검증 회사', signature: '검증 시그니처' }, { id: 'qa-pb', purpose: 'production', production_version_id: 'qa-orphan-version' }, { id: 'qa-pu', purpose: 'production', production_version_id: null }, { id: 'qa-test', purpose: 'test', production_version_id: 'invalid-test-version' }];
    const make = (id, analysis_id, created_at, categories, extra = {}) => ({ id, analysis_id, created_at, categories, comment: '합성 피드백', author: '검증 분석가', ...extra });
    const rows = [
      make('qa-before', 'qa-pa', '2026-11-24T14:59:59.999Z', ['insufficient_evidence']),
      make('qa-start', 'qa-pa', '2026-11-24T15:00:00.000Z', ['insufficient_evidence', 'insufficient_evidence', 'explanation_error']),
      make('qa-end', 'qa-pa', '2026-11-25T14:59:59.999Z', ['evidence_mismatch']),
      make('qa-after', 'qa-pa', '2026-11-25T15:00:00.000Z', ['insufficient_evidence']),
      make('qa-missing-date', 'qa-pa', null, ['other']),
      make('qa-orphan', 'qa-pb', '2026-11-25T02:00:00Z', ['exaggerated_impact', 'inappropriate_severity']),
      make('qa-unrecorded', 'qa-pu', '2026-11-25T02:00:00Z', [], { comment: '의견만 등록' }),
      make('qa-test-row', 'qa-test', '2026-11-25T02:00:00Z', ['insufficient_evidence'], { production_version_id: 'invalid-test-version' }),
      make('qa-local-time', 'qa-pa', '2026-11-25 12:00', ['other']),
      { id: 'qa-legacy', analysisId: 'qa-pa', created_at: '2026-11-25T02:00:00Z', feedback_categories: ['other'], comment: '<img src="https://never-request.invalid/comment" onerror="window.__feedbackInjected=true">', author: '<script>window.__feedbackInjected=true</script>' },
    ];
    const normalized = WAF.feedbackStats.normalize(rows, analyses);
    const filters = { start: '2026-11-25', end: '2026-11-25', purpose: 'production' };
    const filtered = WAF.feedbackStats.filter(normalized, filters);
    let invalidThrows = false; try { WAF.feedbackStats.filter(normalized, { start: '2026-11-26', end: '2026-11-25' }); } catch { invalidThrows = true; }
    window.__feedbackFixture = { analyses, rows }; window.__feedbackInjected = false;
    return { ids: filtered.map(row => row.id).sort(), summary: WAF.feedbackStats.summarize(filtered), categories: WAF.feedbackStats.categoryCounts(filtered), versions: WAF.feedbackStats.versionCounts(filtered), missingVersion: WAF.feedbackStats.filter(normalized, { ...filters, version: '__unrecorded__', purpose: 'all' }).map(row => row.id), test: normalized.find(row => row.id === 'qa-test-row'), legacy: normalized.find(row => row.id === 'qa-legacy'), invalidThrows, validLeap: WAF.feedbackStats.validDate('2024-02-29'), invalidLeap: WAF.feedbackStats.validDate('2026-02-29') };
  });
  const expected = ['qa-end', 'qa-legacy', 'qa-orphan', 'qa-start', 'qa-unrecorded'];
  assert.deepEqual(pure.ids, expected); assert.deepEqual(pure.summary, { feedbackCount: 5, analysisCount: 3, multiCategoryCount: 2, commentOnlyCount: 1 });
  assert.equal(pure.categories.find(row => row.category === 'insufficient_evidence').count, 1);
  assert.equal(pure.categories.find(row => row.category === 'insufficient_evidence').percent, 20);
  assert.ok(pure.categories.reduce((sum, row) => sum + row.percent, 0) > 100);
  assert.deepEqual(pure.missingVersion, ['qa-unrecorded']); assert.equal(pure.test.production_version_id, null);
  assert.equal(pure.legacy.analysis_id, 'qa-pa'); assert.equal(pure.legacy.company, '검증 회사');
  assert.ok(pure.invalidThrows && pure.validLeap && !pure.invalidLeap);
  assert.deepEqual(pure.versions.map(row => [row.id, row.count, row.analysisCount]).sort(), [['__unrecorded__', 1, 1], ['qa-orphan-version', 1, 1], ['qa-v1', 3, 1]].sort());
  result.checks.push('KST boundaries, duplicate-category removal, registration/unique-analysis counts, multi-select denominators, legacy IDs, unknown dates and Test/version isolation are correct.');

  await page.evaluate(() => {
    const { analyses, rows } = window.__feedbackFixture, template = WAF.data.analyses.find(row => row.id === 'ana-007');
    WAF.data.analyses.push(...analyses.map(row => ({ ...template, ...row })));
    WAF.data.feedback = rows; WAF.data.production_versions = [{ id: 'qa-v1', label: '검증 운영 구성' }];
    WAF.go('feedback', { start: '2026-11-25', end: '2026-11-25', purpose: 'production' });
  }); await tick();
  assert.deepEqual(await ids(), expected);
  assert.match(await page.locator('.feedback-missing-date').innerText(), /2건/);
  assert.equal(await page.locator('#feedback-version-summary [data-version="__unrecorded__"]').count(), 1);
  assert.match(await page.locator('#feedback-version-summary [data-version="qa-orphan-version"]').innerText(), /등록 정보가 없는 버전/);
  assert.equal(await page.evaluate(() => window.__feedbackInjected), false);
  assert.equal(await page.locator('#feedback-table img, #feedback-table script').count(), 0);
  await page.locator('#feedback-category-chart [data-category="insufficient_evidence"]').click(); await tick();
  assert.deepEqual(await ids(), ['qa-start']);
  assert.match(page.url(), /category=insufficient_evidence/);
  await page.locator('#feedback-category-chart [data-category="insufficient_evidence"]').click(); await tick();
  assert.deepEqual(await ids(), expected);
  await page.locator('#feedback-version-summary [data-version="__unrecorded__"]').click(); await tick();
  assert.deepEqual(await ids(), ['qa-unrecorded']); assert.match(page.url(), /purpose=production/);
  result.checks.push('Charts filter the same Tabulator rows; missing-version means Production only and unknown version names are not invented.');

  await page.locator('#feedback-filters [name="purpose"]').selectOption('test');
  assert.equal(await page.locator('#feedback-filters [name="version"]').isDisabled(), true);
  assert.equal(await page.locator('#feedback-filters [name="version"]').inputValue(), ''); await apply();
  assert.deepEqual(await ids(), ['qa-test-row']); assert.doesNotMatch(page.url(), /version=/);
  await page.locator('#feedback-filters [name="purpose"]').selectOption('all');
  await page.locator('#feedback-filters [name="version"]').selectOption('qa-v1');
  assert.equal(await page.locator('#feedback-filters [name="purpose"]').inputValue(), 'production'); await apply();
  assert.deepEqual(await ids(), ['qa-end', 'qa-legacy', 'qa-start']);
  const beforeInvalid = page.url();
  await page.locator('#feedback-filters [name="start"]').fill('2026-11-26'); await apply();
  assert.match(await page.locator('#feedback-filter-error').innerText(), /시작일.*종료일/);
  assert.equal(page.url(), beforeInvalid);
  await visit('feedback', { analysis: 'qa-test', start: '2026-11-25', end: '2026-11-25' });
  assert.equal(await page.locator('#feedback-filters [name="purpose"]').inputValue(), 'test');
  await page.locator('[data-action="feedback-all-analyses"]').click(); await tick();
  assert.doesNotMatch(page.url(), /analysis=/); assert.match(page.url(), /purpose=test/);
  result.checks.push('Test clears/disables version, choosing a version restores Production, reversed dates do not apply, and expanding a Test deep link retains Test scope.');

  await visit('feedback', { start: '2025-01-01', end: '2025-01-01', purpose: 'production' });
  assert.deepEqual(await ids(), []);
  assert.doesNotMatch(await page.locator('#feedback-category-chart').innerText(), /0\.0%|NaN|Infinity/);
  assert.match(await page.locator('#feedback-version-summary').innerText(), /없습니다/);
  result.checks.push('An empty period shows an empty state and no misleading percentage or precision rate.');

  await visit('analysis', { id: 'qa-pa', tab: 'result' });
  const beforeAppend = await page.evaluate(() => ({ count: WAF.data.feedback.length, ids: WAF.data.feedback.map(row => row.id) }));
  await page.locator('[data-action="analysis-feedback-new"]').first().click();
  await page.locator('dialog input[value="other"]').check();
  await page.locator('dialog textarea[name="comment"]').fill('추가 합성 의견 <img src="https://never-request.invalid/new" onerror="window.__feedbackInjected=true">');
  await page.locator('[data-action="analysis-feedback-save"]').click(); await tick();
  const appended = await page.evaluate(() => { const row = WAF.data.feedback[0]; WAF.data.feedback_reference_date = WAF.feedbackStats.recordDate(row); return row; });
  assert.equal(await page.evaluate(() => WAF.data.feedback.length), beforeAppend.count + 1);
  assert.ok(await page.evaluate(previous => previous.every(id => WAF.data.feedback.some(row => row.id === id)), beforeAppend.ids));
  assert.equal(appended.production_version_id, 'qa-v1'); assert.ok(appended.created_at);
  await visit('feedback', { analysis: 'qa-pa' });
  assert.ok((await ids()).includes(appended.id));
  await page.locator(`[data-action="feedback-detail"][data-id="${appended.id}"]`).click();
  assert.equal(await page.locator('dialog[open] img, dialog[open] script').count(), 0);
  assert.match(await page.locator('dialog[open]').innerText(), /추가 합성 의견/);
  await page.locator('[data-action="feedback-open-analysis"]').click(); await tick();
  assert.match(page.url(), /analysis\?id=qa-pa/); assert.match(page.url(), /from=feedback/);
  assert.equal(await page.evaluate(() => window.__feedbackInjected), false);
  result.checks.push('Feedback remains append-only, stores registration time/version, is visible in history and safely navigates to its original analysis.');
  assert.equal(result.errors.length, 0); assert.equal(result.network.length, 0);
  await context.close();
} catch (error) {
  result.errors.push(error.stack || error.message);
  if (page && !page.isClosed()) { result.debug = await page.evaluate(() => ({ url: location.href, body: document.body.innerText.slice(0, 6500) })); await page.screenshot({ path: join(output, 'failure.png'), fullPage: true }).catch(() => {}); }
  throw error;
} finally {
  await browser.close(); writeFileSync(join(output, 'review.json'), JSON.stringify(result, null, 2)); console.log(JSON.stringify(result, null, 2));
}
