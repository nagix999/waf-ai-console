// Real React against synthetic APIs; every network request is blocked.
import assert from 'node:assert/strict';
import { existsSync, readdirSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { r5BrowserFixture } from './r5-browser-fixture.mjs';

const output = fileURLToPath(new URL('../../test-results/semantic-alignment', import.meta.url));
mkdirSync(output, { recursive: true });
const mock = r5BrowserFixture().replace('export const api=', `
state.detail.event_name='요청 처리 의미 검토';state.detail.signature='SQL Injection';
state.detail.result.verdict='inconclusive';state.detail.result.threat_analysis.severity='UNKNOWN';
state.detail.result.diagnostics={inconclusive_reasons:['verdict_disagreement']};
state.detail.result.primary={verdict:'true_positive',threat_analysis:{...state.detail.result.threat_analysis,technique_ko:'PRIMARY_INTERPRETATION_CANARY'}};
state.detail.result.verifier={executed:true,output:{verdict:'false_positive'}};
state.detail.result.signature_assessment={relation:'mismatch',explanation_ko:'명확히 일치합니다.'};
Object.assign(evaluation.confusion_matrix,{tp:55,fn:1,abstained_positive:4,tn:55,fp:0,abstained_negative:5,expected_hold_positive:11,expected_hold_negative:9,expected_hold_match:10});
Object.assign(evaluation,{binary_evaluable:120,binary_decided:111});
Object.assign(evaluation.metrics,{accuracy:110/111,precision:1,recall:55/56,f1:110/111,coverage:111/120,balanced_accuracy:(55/56+1)/2,mcc:3025/Math.sqrt(55*56*55*56)});
runs.forEach(run=>{Object.assign(run,{total:150,accepted:150,completed:150});run.ground_truth.approved_count=150;run.ground_truth.sample_count=150;});
working.items.forEach((row,i)=>row.reference_origin=['manual','reference_label','none'][i]);
const readWorking=methods.workingDataset,previewImport=methods.previewTestImport;
methods.workingDataset=async (id,query={})=>{state.lastWorkingQuery=query;const result=await readWorking(id);return {...result,items:result.items.filter(row=>!query.reference_origin||row.reference_origin===query.reference_origin)};};
methods.previewTestImport=async (...args)=>{await new Promise(resolve=>setTimeout(resolve,600));if(state.importFailure){const error=Error('dataset_item_limit');error.status=422;throw error;}return previewImport(...args);};
export const api=`);
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: 'jsx', resolveDir: fileURLToPath(new URL('../src', import.meta.url)) },
  plugins: [{ name: 'offline-api', setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: 'js' })); } }],
  bundle: true, write: false, outfile: join(output, 'bundle.js'), platform: 'browser', format: 'iife', jsx: 'automatic', define: { 'process.env.NODE_ENV': '"production"' }, loader: { '.md': 'text', '.woff2': 'dataurl' }, logLevel: 'silent' });
const archive = join(homedir(), '.cache/uv/archive-v0');
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, 'playwright/driver/package/index.mjs')).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), '.cache/ms-playwright/chromium-1208/chrome-linux64/chrome'), headless: true, args: ['--no-sandbox'] });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, reducedMotion: 'reduce' });
  const errors = [], network = [], screenshots = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => route.request().url().split('#')[0] === 'https://fixture.invalid/' ? route.fulfill({ contentType: 'text/html', body: '<html lang="ko"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' }) : (network.push(route.request().url()), route.abort()));
  await page.goto('https://fixture.invalid/');
  await page.addStyleTag({ content: bundle.outputFiles.find(f => f.path.endsWith('.css')).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(f => f.path.endsWith('.js')).text });
  page.setDefaultTimeout(10000);
  const go = hash => page.evaluate(hash => { location.hash = hash; }, hash);
  const snap = async name => { await page.evaluate(() => scrollTo(0,0)); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, name); await page.screenshot({ path: join(output, name+'.png'), fullPage: !await page.locator('dialog[open]').count() }); screenshots.push(name); };
  const run = '00000008-1111-4111-8111-111111111111', analysis = '11111111-1111-4111-8111-111111111111';
  await page.locator('.r3-overview').waitFor();
  for (const color of ['SK','Light','Dark']) {
    await page.getByRole('button', { name: /^(테마|Theme)$/, exact: true }).click(); await page.getByRole('button', { name: color, exact: true }).click();
    for (const locale of ['KR','EN']) {
      await page.getByRole('button', { name: locale, exact: true }).click();
      await go('#analyses/'+analysis); await page.locator('.decision-hero').waitFor();
      assert.equal(await page.locator('.decision-page-heading h1').innerText(), '요청 처리 의미 검토');
      await page.locator('.decision-technical > details > summary').click();
      const technical = page.locator('.decision-technical');
      assert.match(await technical.innerText(), locale === 'KR' ? /자동 분석 해석이 서로 달랐습니다/ : /different interpretations/);
      assert.equal(await technical.getByText('PRIMARY_INTERPRETATION_CANARY', { exact: true }).isVisible(), false);
      assert.equal(await technical.getByText('명확히 일치합니다.', { exact: true }).isVisible(), false);
      await snap('disagreement-'+locale+'-'+color);
      await go('#evaluate/tests/'+run); await page.getByRole('tab', { name: locale==='KR'?'평가 상세':'Evaluation Details', exact: true }).click();
      const risk = page.locator('.r5-risk-grid'); await risk.waitFor();
      assert.equal(await risk.getByRole('button', { name: locale==='KR'?'보류 답안 → 정탐 확정: 11':'Hold reference → attack: 11', exact: true }).count(), 1);
      assert.equal(await risk.getByRole('button', { name: locale==='KR'?'보류 답안 → 오탐 확정: 9':'Hold reference → benign: 9', exact: true }).count(), 1);
      await page.locator('.r5-statistics > summary').focus(); await page.keyboard.press('Enter');
      await page.locator('.r5-statistics .r5-metric-grid').waitFor({ state: 'visible' });
      await snap('evaluation-'+locale+'-'+color);
      if (color==='Light' && locale==='KR') {
        await risk.getByRole('button', { name: '보류 답안 → 오탐 확정: 9', exact: true }).click();
        await page.getByText('선택한 셀:', { exact: false }).waitFor();
        assert.match(await page.locator('.notice').filter({ hasText: '선택한 셀:' }).innerText(), /보류/);
      }
    }
  }
  await page.getByRole('button', { name: 'KR', exact: true }).click();
  await go('#evaluate/ground-truth'); await page.getByLabel('데이터셋', { exact: true }).selectOption('00000007-1111-4111-8111-111111111111');
  await page.getByLabel('답안 출처', { exact: true }).selectOption('reference_label');
  await page.waitForFunction(() => fixture.lastWorkingQuery?.reference_origin === 'reference_label');
  assert.equal(await page.locator('.r3-case-list tbody tr').count(), 1);
  assert.match(await page.locator('.r3-case-list tbody').innerText(), /참고 라벨/);
  await snap('reference-origin-filter');
  await go('#evaluate/tests/'+run); await page.getByRole('button', { name: '테스트 작업', exact: true }).click(); await page.getByRole('button', { name: '테스트 사례를 정답 데이터에 추가', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '테스트 사례를 정답 데이터에 추가', exact: true });
  await dialog.getByRole('button', { name: '미리보기', exact: true }).click();
  assert.equal(await dialog.getByRole('button', { name: '미리보기 준비 중…', exact: true }).isDisabled(), true);
  await dialog.locator('.r5-import-summary').waitFor();
  assert.equal(await dialog.getByRole('region', { name: '가져오기 미리보기', exact: true }).evaluate(el => el===document.activeElement), true);
  assert.equal(await page.evaluate(() => fixture.writes.filter(x=>x.name==='confirmTestImport').length), 0);
  assert.deepEqual(await dialog.locator('.r5-import-summary strong').allTextContents(), ['1','1','1','0','0']);
  await dialog.getByLabel('문항 구분', { exact: true }).selectOption('missing_reference'); await dialog.getByRole('button', { name: '미리보기', exact: true }).click();
  await dialog.locator('.r5-import-summary').waitFor(); assert.equal(await dialog.getByLabel('문항 구분', { exact: true }).inputValue(), '');
  await snap('import-preview');
  await dialog.getByLabel('저장 위치', { exact: true }).selectOption('append_to_existing_dataset');
  await dialog.getByLabel('데이터셋', { exact: true }).selectOption('00000007-1111-4111-8111-111111111111');
  await dialog.getByRole('button', { name: '미리보기', exact: true }).click(); await dialog.locator('.r5-import-summary').waitFor();
  await dialog.getByLabel('데이터셋 검색', { exact: true }).fill('different');
  assert.equal(await dialog.locator('.r5-import-summary').count(), 0); assert.equal(await dialog.getByLabel('데이터셋', { exact: true }).inputValue(), '');
  await dialog.getByLabel('저장 위치', { exact: true }).selectOption('create_new_dataset');
  await page.evaluate(() => fixture.importFailure=true); await dialog.getByRole('button', { name: '미리보기', exact: true }).click();
  await dialog.getByRole('alert').waitFor(); assert.match(await dialog.getByRole('alert').innerText(), /5,000/);
  assert.equal(await dialog.getByRole('alert').evaluate(el=>el===document.activeElement), true);
  await page.evaluate(() => fixture.importFailure=false); await dialog.getByRole('button', { name: '미리보기', exact: true }).click(); await dialog.locator('.r5-import-summary').waitFor();
  await page.setViewportSize({ width: 390, height: 844 }); await snap('import-mobile');
  await dialog.getByRole('button', { name: '편집 중 데이터에 추가', exact: true }).click(); await dialog.getByRole('button', { name: '정답 데이터 열기', exact: true }).waitFor();
  assert.equal(await page.evaluate(() => fixture.writes.filter(x=>x.name==='confirmTestImport').length), 1);
  await page.keyboard.press('Escape');
  await go('#analyses/'+analysis); await page.locator('.decision-hero').waitFor(); await snap('disagreement-mobile');
  await go('#evaluate/tests/'+run); await page.getByRole('tab', { name: '평가 상세', exact: true }).click(); await page.locator('.r5-risk-grid').waitFor(); await snap('evaluation-mobile');
  assert.deepEqual(errors, []); assert.deepEqual(network, []);
  console.log(JSON.stringify({ screenshots: screenshots.length, checks: 'semantic display, risk direction, origin filter, preview states/counts/focus, reset, safe errors, explicit confirm, KR/EN, themes, mobile; no real API or LLM calls' }));
} finally { await browser.close(); }
