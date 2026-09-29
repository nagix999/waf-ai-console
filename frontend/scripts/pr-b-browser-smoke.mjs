// Actual React and CSS; synthetic fixtures only, all external traffic blocked.
import assert from 'node:assert/strict';
import { existsSync, readdirSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { r5BrowserFixture } from './r5-browser-fixture.mjs';

const output = fileURLToPath(new URL('../../test-results/pr-b-stop', import.meta.url));
mkdirSync(output, { recursive: true });
const mock = r5BrowserFixture().replace('export const api=', `
state.detail.result.signature_assessment={version:'signature-assessment-v2',relation:'partial',matched_points:['요청 경로와 탐지 대상 경로가 일치합니다.','입력 파라미터에서 비교 대상 문자열이 확인됩니다.'],mismatched_points:['시그니처에 설명된 실행 동작은 요청만으로 확인되지 않습니다.'],uncertainty_ko:'탐지 규칙 원문이 없어 실제 매치 조건을 확인할 수 없습니다.',explanation_ko:null};
state.stopCount=0;state.testReads=[];state.runs=runs;
Object.assign(runs[0],{status:'processing',pending:1,processing:1,completed:1,failed:0,canceled:0,can_stop:true,model_test_run_id:null});
Object.assign(runs[1],{status:'processing',processing:3,can_stop:false,model_test_run_id:'model-validation'});
const readTest=methods.testRun;
const stopEvaluation=run=>({...evaluation,evaluable:1,matches:1,binary_evaluable:1,binary_decided:1,support_positive:1,support_negative:0,metrics:{...evaluation.metrics,coverage:1,abstention_rate:0},outcomes:{match:1,canceled:run.stopped_at?2:0,pending:run.stopped_at?0:2},confusion_matrix:{...evaluation.confusion_matrix,tp:1,tn:0,abstained_positive:0}});
methods.testRuns=async()=>({items:runs.map(run=>run.id===runs[0].id?{...run,evaluation_summary:stopEvaluation(run)}:run),total:runs.length});
methods.testRun=async(value,query)=>{state.testReads.push(query);const run=runs.find(r=>r.id===value);return {...await readTest(value),evaluation_summary:stopEvaluation(run),items:cases.map((item,index)=>index===0?item:{...item,status:run.stopped_at?'canceled':index===1?'pending':'processing',verdict:null,summary_ko:null,error_code:run.stopped_at?'test_run_stopped':null,evaluation:{outcome:run.stopped_at?'canceled':'pending',reference_label:item.evaluation.reference_label}}),...(query?.difficulty ? {completed:0,pending:0,processing:0,status:run.stopped_at?'stopped':'completed'} : {})};};
methods.stopTestRun=async(value)=>{state.stopCount++;if(state.stopFailure)throw Error('PRIVATE_SERVER_ERROR'); const run=runs.find(r=>r.id===value);Object.assign(run,{status:'stopped',stopped_at:new Date().toISOString(),stopped_by:'fixture-admin',canceled:run.pending+run.processing,pending:0,processing:0,can_stop:false,official_evaluation_pending:false});return {...run};};
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
  const errors = [], network = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => route.request().url().split('#')[0] === 'https://fixture.invalid/' ? route.fulfill({ contentType: 'text/html', body: '<html lang="ko"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' }) : (network.push(route.request().url()), route.abort()));
  await page.goto('https://fixture.invalid/');
  await page.addStyleTag({ content: bundle.outputFiles.find(f => f.path.endsWith('.css')).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(f => f.path.endsWith('.js')).text });
  page.setDefaultTimeout(10000);
  await page.locator('.r3-overview').waitFor();
  await page.evaluate(() => { location.hash='#analyses/11111111-1111-4111-8111-111111111111/result'; });
  await page.locator('.decision-technical > details > summary').click();
  const comparison = page.locator('.decision-technical-note').filter({has:page.getByRole('heading',{name:/탐지 내용과 요청의 연관성/})});
  const texts = await comparison.locator('li').allTextContents();
  assert.equal(texts.length, 4); assert.ok(texts.every(text => text.length > 10));
  assert.ok(await comparison.locator('li').evaluateAll(items=>items.every(li=>li.getBoundingClientRect().width>150 && li.scrollWidth<=li.clientWidth)));
  await comparison.screenshot({path:join(output,'signature-full-sentences.png')});
  const runId = await page.evaluate(()=>fixture.runs[0].id);
  const go = hash=>page.evaluate(hash=>{location.hash=hash;},hash);
  const snap = async name=>{await page.evaluate(async()=>{window.scrollTo({top:0,behavior:'instant'});await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,name);await page.screenshot({path:join(output,name+'.png'),fullPage:true});};
  for (const theme of ['SK','Light','Dark']) {
    await page.getByRole('button',{name:/^(테마|Theme)$/}).click(); await page.getByRole('button',{name:theme,exact:true}).click();
    for (const locale of ['KR','EN']) {
      await page.getByRole('button',{name:locale,exact:true}).click();
      await go('#overview'); await page.locator('.r3-overview').waitFor();
      await page.evaluate(()=>Object.assign(fixture.runs[0],{status:'processing',pending:1,processing:1,completed:1,canceled:0,stopped_at:null,can_stop:true}));
      await go('#evaluate/tests'); await page.locator('.test-run-history').waitFor();
      const row=page.locator('.test-run-history tbody tr').filter({hasText:'후보 모델 검증'});
      const other=page.locator('.test-run-history tbody tr').filter({hasText:'기준 모델 검증'});
      assert.equal(await other.getByRole('button',{name:/작업|Actions for/}).count(),0,'no model-validation stop');
      const action=row.getByRole('button',{name:/작업|Actions for/});
      await action.focus();await page.keyboard.press('Enter');
      await page.getByRole('button',{name:locale==='KR'?'테스트 중지':'Stop test',exact:true}).click();
      let dialog=page.getByRole('dialog',{name:locale==='KR'?'테스트 중지':'Stop test',exact:true});
      await dialog.locator('.test-stop-counts').waitFor();
      assert.deepEqual(await dialog.locator('dd').allTextContents(),['1','2']);
      // Keyboard focus remains within the native modal and Escape makes no write.
      await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>Boolean(document.activeElement?.closest('dialog[open]'))),true);
      const before=await page.evaluate(()=>fixture.stopCount);
      await page.keyboard.press('Escape'); await dialog.waitFor({state:'hidden'});
      assert.equal(await page.evaluate(()=>fixture.stopCount),before);
      await snap('list-'+locale+'-'+theme);
      await go('#evaluate/tests/'+runId);await page.locator('.test-run-detail').waitFor();
      const stopButton=page.locator('.test-run-header').getByRole('button',{name:locale==='KR'?'테스트 중지':'Stop test',exact:true});
      await stopButton.click();dialog=page.getByRole('dialog',{name:locale==='KR'?'테스트 중지':'Stop test',exact:true});
      await dialog.locator('.test-stop-counts').waitFor();
      const contrast = await dialog.locator('.test-stop-confirm').evaluate(button => {
        const style = getComputedStyle(button);
        const luminance = color => color.match(/[\d.]+/g).slice(0, 3).map(Number).map(value => {
          const linear = value / 255; return linear <= 0.04045 ? linear / 12.92 : ((linear + 0.055) / 1.055) ** 2.4;
        }).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
        const foreground = luminance(style.color), background = luminance(style.backgroundColor);
        return (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05);
      });
      assert.ok(contrast >= 4.5, `${theme} stop button contrast: ${contrast}`);
      await snap('confirm-'+locale+'-'+theme);
      await dialog.getByRole('button',{name:locale==='KR'?'테스트 중지':'Stop test',exact:true}).evaluate(button=>{button.click();button.click();});
      await page.locator('.test-stopped-notice').waitFor();
      assert.equal(await page.evaluate(()=>fixture.stopCount),before+1);
      assert.equal(await stopButton.count(),0);assert.equal(await page.getByRole('button',{name:/^(운영 반영 검토|Production Review)$/}).count(),0);
      assert.equal(await page.locator('.test-run-cases').getByRole('button',{name:'오류 정보',exact:true}).count(),0);
      await snap('stopped-'+locale+'-'+theme);
      await page.getByRole('tab',{name:locale==='KR'?'평가 상세':'Evaluation Details',exact:true}).click();
      assert.match(await page.locator('.test-stopped-notice').innerText(),locale==='KR'?/전체 테스트 결과가 아닙니다/:/not the full test/);
      assert.equal(await page.locator('.r5-counts > div').filter({hasText:locale==='KR'?'중지됨 · 평가 제외':'Stopped · excluded'}).locator('dd').innerText(),'2');
      await snap('metrics-'+locale+'-'+theme);
      if (theme==='Light' && locale==='KR') {await page.setViewportSize({width:390,height:844});await snap('stopped-mobile');await page.setViewportSize({width:1440,height:960});}
    }
  }
  // Even when a filtered view reports no running cases, its live can_stop flag
  // exposes the action and the dialog re-reads unfiltered global counts.
  await page.getByRole('button',{name:'KR',exact:true}).click();
  await go('#overview');await page.locator('.r3-overview').waitFor();
  await page.evaluate(()=>{Object.assign(fixture.runs[0],{status:'processing',pending:1,processing:1,completed:1,canceled:0,stopped_at:null,can_stop:true});fixture.stopFailure=true;});
  await go('#evaluate/tests/'+runId);await page.locator('.test-run-detail').waitFor();
  await page.getByRole('button',{name:'테스트 작업',exact:true}).click();
  await page.getByRole('button',{name:'난이도·유형 필터',exact:true}).click();
  await page.locator('.test-run-scope select[name="difficulty"]').selectOption('value:hard');
  await page.locator('.r5-test-progress').filter({hasText:'처리 완료 0 / 3'}).waitFor();
  await page.keyboard.press('Escape');
  const filteredHash=await page.evaluate(()=>location.hash);
  await page.locator('.test-run-header').getByRole('button',{name:'테스트 중지',exact:true}).click();
  const failedDialog=page.getByRole('dialog',{name:'테스트 중지',exact:true});await failedDialog.locator('.test-stop-counts').waitFor();
  assert.deepEqual(await failedDialog.locator('dd').allTextContents(),['1','2']);
  assert.deepEqual(await page.evaluate(()=>fixture.testReads.at(-1)),{limit:1,reference_basis:'initial'});
  const calls=await page.evaluate(()=>fixture.stopCount);
  await failedDialog.getByRole('button',{name:'테스트 중지',exact:true}).click();
  await failedDialog.getByRole('alert').waitFor();
  assert.doesNotMatch(await failedDialog.innerText(),/PRIVATE_SERVER_ERROR/);
  assert.match(await failedDialog.innerText(),/자동 재전송하지 않습니다/);
  assert.equal(await failedDialog.getByRole('button',{name:'테스트 중지',exact:true}).isDisabled(),true);
  assert.equal(await page.evaluate(()=>fixture.stopCount),calls+1);
  await failedDialog.getByRole('button',{name:'닫기',exact:true}).click();
  assert.equal(await page.evaluate(()=>location.hash),filteredHash);
  await page.evaluate(()=>{fixture.stopFailure=false;});
  await page.locator('.test-run-header').getByRole('button',{name:'테스트 중지',exact:true}).click();
  await failedDialog.locator('.test-stop-counts').waitFor();
  await failedDialog.getByRole('button',{name:'테스트 중지',exact:true}).click();
  await page.locator('.test-stopped-notice').waitFor();
  assert.match(await page.locator('.test-run-detail').innerText(),/적용된 평가 범위: hard/);
  assert.equal(await page.evaluate(()=>location.hash),filteredHash);
  assert.deepEqual(errors, []); assert.deepEqual(network, []);
  console.log(JSON.stringify({screenshots:26,checks:'full signature sentences, list/detail stop, model validation excluded, counts, keyboard/focus, stop once, partial metrics warning, SK/Light/Dark, KR/EN, mobile overflow; no external traffic'}));
} finally { await browser.close(); }
