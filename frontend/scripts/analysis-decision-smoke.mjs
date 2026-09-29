// Real React, synthetic APIs only. No live service, LLM, credentials or downloads.
import assert from 'node:assert/strict';
import { existsSync, readdirSync, mkdirSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { r5BrowserFixture } from './r5-browser-fixture.mjs';

const output = fileURLToPath(new URL('../../test-results/analysis-decision-v2', import.meta.url));
mkdirSync(output, { recursive: true });
const archive = join(homedir(), '.cache/uv/archive-v0');
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, 'playwright/driver/package/index.mjs')).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const fixtureSource = r5BrowserFixture().replace('export const api=', `
Object.assign(methods, {
 referenceSelection: async ids => ({items: ids.map(analysis_id => ({analysis_id, expected_revision: 0, verdict: null}))}),
 saveReferences: async payload => { state.writes.push({name:'saveReferences', payload}); state.detail.evaluation={outcome:'expected_abstention_match', reference_label:{verdict:payload.verdict, source_kind:'reference', ai_visible:true, revision:1}}; return {applied_count:1}; }
});
state.detail.result.threat_analysis.severity='UNKNOWN';
export const api=`);
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: 'jsx', resolveDir: fileURLToPath(new URL('../src', import.meta.url)) },
  plugins: [{ name: 'offline-api', setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: fixtureSource, loader: 'js' })); } }],
  bundle: true, write: false, outfile: join(output, 'bundle.js'), platform: 'browser', format: 'iife', jsx: 'automatic', define: { 'process.env.NODE_ENV': '"production"' }, loader: { '.md': 'text', '.woff2': 'dataurl' }, logLevel: 'silent' });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), '.cache/ms-playwright/chromium-1208/chrome-linux64/chrome'), headless: true, args: ['--no-sandbox'] });
const id = '11111111-1111-4111-8111-111111111111', second = '22222222-2222-4222-8222-222222222222';
const errors = [], network = [], images = [];
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, reducedMotion: 'reduce' });
  page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => route.request().url().split('#')[0] === 'https://fixture.invalid/' ? route.fulfill({ contentType: 'text/html', body: '<html lang="ko"><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div></body></html>' }) : (network.push(route.request().url()), route.abort()));
  await page.goto('https://fixture.invalid/#analyses/' + id);
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith('.css')).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith('.js')).text });
  await page.evaluate(() => {
    fixture.downloads = [];
    window.fetch = async (path, options) => {
      if (!/^\/api\/v1\/analyses\/[0-9a-f-]+\/report\.(pdf|xlsx)/.test(path)) throw Error('Unexpected fixture fetch');
      fixture.downloads.push({ path, options });
      return new Response(path.includes('.pdf') ? '%PDF-fixture' : 'PK-fixture', { headers: { 'Content-Type': path.includes('.pdf') ? 'application/pdf' : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' } });
    };
  });
  const tabs = page.locator('.inference-detail > .detail-tabs');
  const tab = name => tabs.getByRole('tab', { name, exact: true }).click();
  const reads = name => page.evaluate(name => fixture.reads.filter(call => call.name === name).length, name);
  const go = async hash => { await page.evaluate(hash => { location.hash = hash; }, hash); };
  const screenshot = async name => {
    await page.evaluate(() => scrollTo(0, 0));
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, name + ' page overflow');
    await page.screenshot({ path: join(output, name + '.png'), fullPage: !await page.locator('dialog[open]').count() }); images.push(name);
  };
  const theme = async name => { await page.getByRole('button', { name: /^(테마|Theme)$/, exact: true }).click(); await page.getByRole('button', { name, exact: true }).click(); };
  await tabs.waitFor();
  assert.deepEqual(await tabs.getByRole('tab').allTextContents(), ['판정', '근거·입력', '실행', '보고서']);
  assert.equal(await reads('rawEvent'), 0); assert.equal(await reads('agentRuns'), 0);
  assert.equal(await page.locator('.inference-overview:visible').count(), 0);
  assert.equal(await page.locator('.decision-hero-summary:visible').count(), 1);
  assert.equal(await page.locator('.decision-technical > details').getAttribute('open'), null);
  await tabs.getByRole('tab', { name: '판정', exact: true }).focus(); await page.keyboard.press('ArrowRight');
  assert.equal(await page.evaluate(() => document.activeElement.textContent), '근거·입력');
  assert.equal(await reads('rawEvent'), 0); assert.equal(await reads('agentRuns'), 0);
  await screenshot('hold-desktop-sk');
  await page.getByRole('button', { name: '원문에서 보기 →', exact: true }).first().click();
  await page.locator('mark:visible').waitFor();
  assert.equal(await page.locator('mark:visible').innerText(), 'code_verifier=fixture');
  assert.equal(await reads('rawEvent'), 1);
  assert.equal(await tabs.getByRole('tab', { name: '근거·입력', exact: true }).evaluate(el => el === document.activeElement), true);
  await screenshot('evidence-desktop-sk');
  const absent = page.locator('.decision-source-list button').filter({ hasText: 'missing-quote-canary' });
  await absent.click(); await page.getByText('이 필드에서 발췌문과 같은 문구를 찾지 못했습니다.', { exact: false }).waitFor();
  assert.equal(await absent.evaluate(el => el === document.activeElement), true);
  assert.equal(await page.locator('mark:visible').count(), 0);
  const metadata = page.locator('.decision-source-list button').filter({ hasText: 'WAF 조치' });
  await metadata.click(); await page.waitForFunction(() => document.querySelector('.input-fields-grid mark')?.textContent === 'D');
  assert.equal(await reads('rawEvent'), 1);
  await page.getByRole('tab', { name: '인코딩·난독화', exact: true }).click();
  await screenshot('decoding-desktop-sk');
  const stored = await page.evaluate(() => JSON.stringify({ url: location.href, history: history.state, local: { ...localStorage }, session: { ...sessionStorage } }));
  assert.doesNotMatch(stored, /code_verifier|private-fixture-cookie|missing-quote/);
  assert.equal(await page.evaluate(() => window.injected), undefined);
  await tab('실행'); await page.getByRole('button', { name: '전체 단계 보기', exact: true }).waitFor();
  assert.equal(await reads('agentRuns'), 1); assert.equal(await page.locator('.inspection-agent-grid:visible').count(), 0);
  await screenshot('execution-summary-desktop-sk');
  await page.getByRole('button', { name: '전체 단계 보기', exact: true }).click();
  await page.locator('.inspection-agent-grid').waitFor();
  assert.match(await page.locator('.inspection-repair-summary').innerText(), /출력 형식 재시도\s*2회/);
  await page.getByRole('tablist', { name: '단계 자료', exact: true }).getByRole('tab', { name: '분석 입력', exact: true }).click();
  await page.getByText('trace-input-canary', { exact: false }).waitFor();
  await screenshot('execution-trace-desktop-sk');
  await tab('판정'); await tab('실행'); await page.getByText('trace-input-canary', { exact: false }).waitFor();
  await page.locator('.decision-json summary').click(); await page.getByRole('region', { name: '결과 JSON 열람' }).waitFor();
  await screenshot('execution-json-desktop-sk');
  await go('#analyses/' + id + '/json'); await page.getByRole('region', { name: '결과 JSON 열람' }).waitFor();
  assert.equal(await tabs.getByRole('tab', { name: '실행', exact: true }).getAttribute('aria-selected'), 'true');
  await tab('보고서'); await page.getByRole('button', { name: 'Markdown 원본', exact: true }).click();
  await page.getByRole('checkbox', { name: '평가·실행 부록 포함' }).check();
  for (const format of ['PDF', 'Excel']) {
    await page.getByRole('button', { name: '보고서 다운로드', exact: true }).click();
    const download = page.waitForEvent('download'); await page.getByRole('button', { name: format + ' 다운로드', exact: true }).click(); await download;
  }
  assert.equal(await reads('rawEvent'), 1);
  const files = await page.evaluate(() => fixture.downloads);
  assert.match(files[0].path, /include_appendix=true/); assert.match(files[0].path, /include_decoding=true/); assert.match(files[1].path, /report\.xlsx$/);
  await page.getByRole('button', { name: '보고서 보기', exact: true }).click(); await screenshot('report-desktop-sk');
  await tab('판정');
  await page.getByRole('button', { name: '답안·데이터 관리', exact: true }).click(); await page.getByRole('button', { name: '참고 답안 입력', exact: true }).click();
  const reference = page.getByRole('dialog', { name: '참고 답안 입력', exact: true });
  await reference.getByRole('combobox', { name: '참고 답안', exact: true }).selectOption('inconclusive');
  await reference.getByRole('button', { name: '답안 저장', exact: true }).click(); await page.getByText('1건의 참고 답안을 저장했습니다.', { exact: true }).waitFor();
  await page.locator('.decision-context .compact-evaluation-detail').waitFor();
  // Re-mount with synthetic decisive, held and failure cases, in all themes.
  await page.evaluate(() => { fixture.originalDetail = structuredClone(fixture.detail); });
  for (const verdict of ['true_positive', 'false_positive', 'inconclusive', 'failed', 'pending']) {
    await go('#overview'); await page.locator('.r3-overview').waitFor();
    await page.evaluate(verdict => {
      const d = structuredClone(fixture.originalDetail); fixture.detail = d;
      d.status = ['failed', 'pending'].includes(verdict) ? verdict : 'completed';
      if (d.status !== 'completed') { d.result = null; d.error_code = verdict === 'failed' ? 'primary_agent_failed' : null; return; }
      d.result.verdict = verdict; d.result.threat_analysis.severity = verdict === 'true_positive' ? 'CRITICAL' : verdict === 'false_positive' ? 'NONE' : 'UNKNOWN';
      d.result.primary = {verdict: verdict === 'inconclusive' ? 'true_positive' : verdict};
      d.result.verifier = {executed:true,output:{verdict:verdict === 'inconclusive' ? 'false_positive' : verdict}};
      d.evaluation.outcome = verdict === 'inconclusive' ? 'expected_abstention_match' : 'expected_abstention_mismatch';
      if (verdict !== 'inconclusive') {
        d.result.diagnostics = {}; d.result.analyst_guidance.summary_ko = verdict === 'true_positive' ? '입력 값에 실행 가능한 공격 구문이 포함되어 있습니다. 원문과 처리 문맥을 함께 검토했습니다.' : '탐지된 문자열은 파일명에 포함된 일반 값입니다. 요청에서 실행 가능한 공격 구문은 확인되지 않았습니다.';
        d.result.analyst_guidance.checks = []; d.result.analyst_assessment.decision_issues = [];
      }
    }, verdict);
    await go('#analyses/' + id); await tabs.waitFor();
    if (verdict === 'failed') {
      await page.getByRole('button', { name: '재실행', exact: true }).click(); await page.getByRole('dialog', { name: '실패한 분석 재실행', exact: true }).waitFor(); await screenshot('retry-dialog'); await page.keyboard.press('Escape');
    }
    for (const color of ['SK', 'Light', 'Dark']) {
      await theme(color);
      for (const width of [1440, 390]) { await page.setViewportSize({ width, height: width === 1440 ? 960 : 844 }); await screenshot(verdict + '-' + width + '-' + color.toLowerCase()); }
      await page.setViewportSize({ width: 1440, height: 960 });
    }
  }
  await go('#overview'); await page.locator('.r3-overview').waitFor(); await page.evaluate(() => { fixture.detail = fixture.originalDetail; });
  await go('#analyses/' + id); await tabs.waitFor();
  await page.getByRole('button', { name: 'EN', exact: true }).click();
  assert.deepEqual(await tabs.getByRole('tab').allTextContents(), ['Decision', 'Evidence & input', 'Execution', 'Report']);
  for (const color of ['SK', 'Light', 'Dark']) { await theme(color); await screenshot('decision-en-' + color.toLowerCase()); }
  await page.getByRole('button', { name: 'KR', exact: true }).click();
  for (const width of [360, 768, 1024]) { await page.setViewportSize({ width, height: 960 }); for (const name of ['판정', '근거·입력', '실행']) { await tab(name); await screenshot('responsive-' + width + '-' + ({ 판정: 'decision', '근거·입력': 'evidence', 실행: 'execution' })[name]); } }
  // Audited late responses from another analysis must never replace this input.
  await page.evaluate(key => { fixture.holdRaw = true; location.hash = '#analyses/' + key + '/raw'; }, second);
  await page.waitForFunction(() => typeof fixture.resolveRaw === 'function');
  await page.evaluate(key => { fixture.holdRaw = false; location.hash = '#analyses/' + key; }, id); await tabs.waitFor();
  await page.evaluate(() => fixture.resolveRaw());
  await page.evaluate(() => { fixture.rawError = true; }); await tab('근거·입력'); await page.getByText('입력을 조회하지 못했습니다.', { exact: true }).waitFor();
  assert.doesNotMatch(await page.locator('body').innerText(), /SECOND-INPUT-CANARY|private-upstream-error/);
  await page.evaluate(() => { fixture.rawError = false; }); await page.getByRole('button', { name: '다시 시도', exact: true }).click(); await page.getByRole('region', { name: 'HTTP 원문 열람' }).waitFor();
  assert.deepEqual(errors, []); assert.deepEqual(network, []);
  assert.deepEqual(await page.evaluate(() => fixture.unknown), []);
  writeFileSync(join(output, 'index.html'), '<meta charset="utf-8"><title>판정 결과 v2 검토</title><style>body{font:14px system-ui;background:#eef0f3;margin:24px}section{margin-bottom:32px}img{max-width:100%;border:1px solid #ccd1d8}</style>' + images.map(name => `<section><h2>${name}</h2><a href="${name}.png"><img loading="lazy" src="${name}.png"></a></section>`).join(''));
  console.log('Decision UI: four tabs, evidence/input audit, keyboard, legacy links, JSON, labels, reports, retry dialog, themes/locales and responsive screenshots passed:', images.length);
} finally { await browser.close(); }
