// Synthetic, offline browser regression. No live API, operational data or LLM calls.
import assert from 'node:assert/strict';
import { existsSync, readdirSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { r5BrowserFixture } from './r5-browser-fixture.mjs';

const output = fileURLToPath(new URL('../../test-results/single-analysis-copy', import.meta.url));
mkdirSync(output, { recursive: true });
const productionId = '11111111-1111-4111-8111-111111111111';
const id = n => `${String(n).padStart(8, '0')}-1111-4111-8111-111111111111`;
const copyHash = source => `#evaluate/tests/new?source_analysis_id=${source}`;
const marker = 'SYNTHETIC_COPY_PRIVATE';
const mock = r5BrowserFixture().replace('get(target,name){', "get(target,name){if(name==='analysisEvidenceMeta')return()=>null;").replace('export const api=', `
state.singleRequests=[];state.templateReads=[];
const copyEvent={event_id:'SYNTHETIC_COPY_PRIVATE-event',company_name:'Synthetic copy company',waf_vendor:'synthetic-vendor',src_ip:'192.0.2.10',dest_ip:'198.51.100.20',src_port:0,dest_port:443,signature:null,event_name:'',waf_action:'D',payload:'POST /SYNTHETIC_COPY_PRIVATE HTTP/1.1\\r\\nHost: fixture.invalid\\r\\n\\r\\n{"message":"synthetic only"}',extra_fields:{nested:{enabled:false,score:0,missing:null,tags:['fixture',null]}},custom_root:{zero:0,disabled:false,nullable:null}};
profiles.push({...profiles[1],id:id(99),name:'Approved external fixture',provider:'openai'});
methods.singleTestTemplate=async source=>{state.templateReads.push(source);if(source===id(404)){const error=Error('PRIVATE_TEMPLATE_ERROR');error.status=404;throw error;}return {source_analysis_id:source,internal_only:source==='${productionId}'||source===id(42),name:'Source single test',event:copyEvent,initial_assessment:{initial_verdict:'false_positive',initial_probability:0,initial_model_version:'synthetic-initial'},expected_verdict:source===id(43)?null:'true_positive',difficulty:'hard',test_category:'synthetic-category',case_name:'Synthetic copied case'};};
methods.createTestRun=async payload=>{state.singleRequests.push(payload);return runs[0];};
export const api=`);
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';import './workspacePolish.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: 'jsx', resolveDir: fileURLToPath(new URL('../src', import.meta.url)) },
  plugins: [{ name: 'offline-api', setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: 'js' })); } }],
  bundle: true, write: false, outfile: join(output, 'bundle.js'), platform: 'browser', format: 'iife', jsx: 'automatic', define: { 'process.env.NODE_ENV': '"production"' }, loader: { '.md': 'text', '.woff2': 'dataurl' }, logLevel: 'silent' });
const archive = join(homedir(), '.cache/uv/archive-v0');
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, 'playwright/driver/package/index.mjs')).find(existsSync);
if (!driver) throw Error('An existing development browser is required; no download is attempted.');
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), '.cache/ms-playwright/chromium-1208/chrome-linux64/chrome'), headless: true, args: ['--no-sandbox'] });
let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, reducedMotion: 'reduce' });
  page.setDefaultTimeout(10000);
  const errors = [], traffic = [], checks = []; let screenshots = 0;
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => route.request().url().split('#')[0] === 'https://fixture.invalid/'
    ? route.fulfill({ contentType: 'text/html', body: '<html lang="ko"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' })
    : (traffic.push(route.request().url()), route.abort()));
  const mount = async hash => {
    await page.goto('https://fixture.invalid/' + hash);
    await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith('.css')).text });
    await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith('.js')).text });
  };
  const go = hash => page.evaluate(hash => { location.hash = hash; }, hash);
  const form = () => page.locator('form.form-panel');
  const ready = async source => {
    await form().waitFor();
    await page.waitForFunction(source => location.hash === source, copyHash(source));
    await page.waitForFunction(() => document.querySelector('form.form-panel button[type="submit"]')?.disabled === false);
  };
  const stored = () => page.evaluate(() => JSON.stringify({ url: location.href, history: history.state, local: { ...localStorage }, session: { ...sessionStorage } }));
  const snap = async name => { await page.evaluate(() => window.scrollTo(0, 0)); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true); await page.screenshot({ path: join(output, name + '.png'), fullPage: true }); screenshots++; };
  const assertFields = async kr => {
    const names = kr ? ['회사명', 'WAF 벤더', '출발지 IP', '목적지 IP', '이벤트 ID', '출발지 포트', '목적지 포트'] : ['Company', 'WAF vendor', 'Source IP', 'Destination IP', 'Event ID', 'Source port', 'Destination port'];
    for (const [index, value] of ['Synthetic copy company', 'synthetic-vendor', '192.0.2.10', '198.51.100.20', marker + '-event', '0', '443'].entries()) assert.equal(await form().getByLabel(names[index], { exact: true }).inputValue(), value);
    assert.equal(await page.getByRole('radio', { name: kr ? /개발 테스트/ : /Development Test/ }).isChecked(), true);
    assert.equal(await page.getByRole('tab', { name: kr ? '단건 분석' : 'Single Analysis', exact: true }).getAttribute('aria-selected'), 'true');
    assert.equal(await form().getByLabel(kr ? '테스트명' : 'Test name', { exact: true }).inputValue(), 'Source single test');
    assert.equal(await form().getByLabel(kr ? '1차 신뢰도 (0~1)' : 'Initial confidence (0–1)', { exact: true }).inputValue(), '0');
    assert.equal(await form().getByLabel(kr ? '1차 모델 버전' : 'Initial model version', { exact: true }).inputValue(), 'synthetic-initial');
    assert.equal(await form().getByRole('combobox', { name: kr ? /^참고 판정 \(선택\)/ : /^Reference Verdict \(optional\)/ }).inputValue(), 'true_positive');
    assert.match(await form().getByRole('textbox', { name: kr ? /^HTTP 원문/ : /^Raw HTTP/ }).inputValue(), /SYNTHETIC_COPY_PRIVATE/);
    const extra = JSON.parse(await form().getByRole('textbox', { name: kr ? /^스키마 추가 필드 \(JSON\)/ : /^Additional schema fields \(JSON\)/ }).inputValue());
    assert.deepEqual(extra, { extra_fields: { nested: { enabled: false, score: 0, missing: null, tags: ['fixture', null] } }, custom_root: { zero: 0, disabled: false, nullable: null } });
    assert.equal(await page.evaluate(() => fixture.singleRequests.length), 0);
    assert.doesNotMatch(await stored(), new RegExp(marker));
  };

  await mount('#operate/inference');
  await page.getByRole('button', { name: '분석 작업', exact: true }).first().click();
  await page.getByRole('button', { name: '단건 분석으로 가져오기', exact: true }).click();
  await ready(productionId); await assertFields(true);
  // Keyboard traversal reaches the probability field without triggering submission.
  await form().getByRole('combobox', { name: /^1차 판정/ }).focus(); await page.keyboard.press('Tab');
  assert.equal(await page.locator(':focus').getAttribute('type'), 'number');
  await snap('production-copy-KR-SK');
  checks.push('Production row action, complete input and zero confidence prefill, development/direct, no automatic submission, keyboard');

  // A real page reload re-fetches by UUID; no raw input is persisted in storage.
  await mount(copyHash(productionId)); await ready(productionId); await assertFields(true);
  assert.deepEqual(await page.evaluate(() => fixture.templateReads), [productionId]);
  const countBefore = await page.evaluate(() => fixture.templateReads.length);
  await go('#evaluate/tests/new');
  await page.waitForFunction(() => location.hash === '#evaluate/tests/new');
  assert.equal(await form().getByLabel('회사명', { exact: true }).inputValue(), '');
  await page.goBack(); await ready(productionId); await assertFields(true);
  assert.ok(await page.evaluate(() => fixture.templateReads.length) > countBefore);
  await page.goForward(); await page.waitForFunction(() => location.hash === '#evaluate/tests/new');
  assert.equal(await form().getByLabel('HTTP 원문', { exact: true }).inputValue(), '');
  checks.push('reload and back/forward use source UUID only, normal new Test clears imported input');

  await go('#evaluate/tests/' + id(8)); await page.locator('.test-run-detail').waitFor();
  await page.getByRole('button', { name: '문항 작업', exact: true }).first().click();
  await page.getByRole('button', { name: '단건 분석으로 가져오기', exact: true }).click();
  await ready(id(41)); await assertFields(true);
  await page.getByRole('button', { name: 'EN', exact: true }).click(); await assertFields(false);
  await page.getByRole('button', { name: 'Theme', exact: true }).click(); await page.getByRole('button', { name: 'Dark', exact: true }).click();
  await snap('test-copy-EN-Dark');
  checks.push('Test case row action, KR/EN and SK/Dark');

  await go('#operate/inference/' + productionId + '/result');
  await page.getByRole('button', { name: 'Use in Single Analysis', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Use in Single Analysis', exact: true }).click();
  await ready(productionId); await assertFields(false);
  const primary = page.locator('.candidate-configuration select').first();
  await primary.selectOption(id(99));
  await page.waitForFunction(() => document.querySelector('form.form-panel button[type="submit"]')?.disabled === true);
  await form().getByText('This input is internal-only. Select vLLM for every model role.', { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => fixture.singleRequests.length), 0);
  await primary.selectOption(id(1)); await ready(productionId);
  const editedPayload = 'GET /SYNTHETIC_COPY_PRIVATE-edited HTTP/1.1\nHost: fixture.invalid\n\n';
  await form().getByRole('textbox', { name: /^Raw HTTP/ }).fill(editedPayload);
  await form().getByLabel('Company', { exact: true }).fill('Edited synthetic company');
  await form().getByRole('button', { name: 'Start analysis', exact: true }).click();
  await page.locator('.test-run-detail').waitFor();
  const request = await page.evaluate(() => fixture.singleRequests.at(-1));
  assert.equal(request.source_analysis_id, productionId); assert.equal(request.event.company_name, 'Edited synthetic company'); assert.equal(request.event.payload, editedPayload);
  assert.equal(request.event.src_port, 0); assert.equal(request.event.signature, null); assert.equal(request.event.event_name, '');
  assert.equal(request.initial_probability, 0); assert.equal(request.initial_verdict, 'false_positive'); assert.equal(request.expected_verdict, 'true_positive');
  assert.deepEqual(request.event.extra_fields.nested, { enabled: false, score: 0, missing: null, tags: ['fixture', null] });
  assert.equal(Object.hasOwn(request.event, 'initial_probability'), false); assert.equal(Object.hasOwn(request.event, 'source_analysis_id'), false);
  assert.doesNotMatch(await stored(), new RegExp(marker));
  checks.push('detail action, internal-only OpenAI block and vLLM enable, edited request preserves pointer, null/false/zero types and separated metadata');

  await go(copyHash(id(43))); await ready(id(43));
  assert.equal(await form().getByRole('combobox', { name: /^Reference Verdict \(optional\)/ }).inputValue(), '');
  await go(copyHash(id(404))); await page.getByRole('alert').filter({ hasText: 'Could not load the original input.' }).waitFor();
  assert.equal(await form().count(), 0); assert.equal(await page.getByRole('button', { name: 'Start analysis', exact: true }).count(), 0);
  assert.doesNotMatch(await page.locator('body').innerText(), /PRIVATE_TEMPLATE_ERROR/);
  await snap('source-unavailable-EN-Dark');
  checks.push('AI verdict never becomes a reference answer, missing source cannot submit empty input, safe error');
  assert.deepEqual(traffic, []); assert.deepEqual(errors, []); assert.deepEqual(await page.evaluate(() => fixture.unknown), []);
  console.log(JSON.stringify({ result: 'passed', checks, screenshots, output, liveApiCalls: 0, llmCalls: 0 }));
} catch (error) {
  if (page) { await page.screenshot({ path: join(output, 'failure.png'), fullPage: true }); console.error('Current URL:', page.url()); }
  throw error;
} finally { await browser.close(); }
