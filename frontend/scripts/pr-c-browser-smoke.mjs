// Actual React/CSS, synthetic API fixtures only. No external/model traffic.
import assert from 'node:assert/strict';
import { existsSync, readdirSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { r5BrowserFixture } from './r5-browser-fixture.mjs';

const output = fileURLToPath(new URL('../../test-results/pr-c-datasets', import.meta.url));
mkdirSync(output, { recursive: true });
const mock = r5BrowserFixture().replace('export const api=', `
state.catalogReads=[];state.draftReads=[];state.productionReads=0;state.historyReads=0;
state.copyCount=0;state.catalogFailure=false;state.productionFailure=false;state.noEvaluation=false;
const catalogRows=Array.from({length:27},(_,i)=>({id:id(200+i),name:'회귀 검증 '+String(i+1).padStart(2,'0'),description:'요청 입력과 기대 판정을 관리하는 예시 평가 문항입니다.',ready_count:3,needs_attention_count:1,excluded_count:1,working_change_count:2,total:5,published_case_count:4,latest_published_revision:{id:id(400+i),revision:4,total:4}}));
Object.assign(catalogRows[4],{id:dataset.id,name:'WAF 핵심 회귀셋',description:'탐지 규칙과 요청 문맥을 비교하는 예시 문항 모음입니다.'});
catalogRows[0].latest_published_revision=null;catalogRows[0].published_case_count=0;
Object.assign(working,{name:catalogRows[4].name,total:5,filtered_total:5,counts:{ready:3,needs_attention:1,excluded:1},working_changes_count:2,changes:{added:1,changed:1,removed:0,unchanged:3},published_revisions:[{id:id(74),revision:4,total:4},{id:id(73),revision:3,total:3}],latest_published_revision_id:id(74)});
working.items.push({...working.items[0],item_id:id(25),id:id(25),case_name:'경로 정규화 비교'},{...working.items[0],item_id:id(26),id:id(26),case_name:'JSON 문자열 경계'});
Object.assign(runs[0],{status:'processing',accepting_items:false,completed:1,pending:1,processing:1});
state.runs=runs;state.datasetId=dataset.id;state.catalogRows=catalogRows;
methods.searchValidationDatasets=async(query)=>{state.catalogReads.push(query);if(state.catalogFailure)throw Error('PRIVATE_CATALOG');const rows=catalogRows.filter(row=>row.name.includes(query.query||''));return {items:rows.slice(query.offset||0,(query.offset||0)+(query.limit||20)),total:rows.length};};
methods.productionConfiguration=async()=>{state.productionReads++;if(state.productionFailure)throw Error('PRIVATE_PRODUCTION');return {...production,evaluation:state.noEvaluation?null:{...official,summary:{...official.summary,ground_truth:{...official.summary.ground_truth,dataset_id:dataset.id,dataset_revision:3,dataset_revision_id:id(73)}}}};};
methods.productionEvaluations=async()=>{state.historyReads++;return {items:[{summary:{ground_truth:{dataset_id:catalogRows[0].id,dataset_revision:99}}}]};};
methods.workingDataset=async(value,query)=>{state.draftReads.push({id:value,query});return {...working,id:value,name:catalogRows.find(row=>row.id===value)?.name||working.name};};
methods.previewTestImport=async(_,payload)=>{state.writes.push({name:'previewTestImport',payload});if(runs[0].accepting_items)throw Error('test_ingestion_open');return {preview_token:id(91),source_total:3,importable:3,new_count:2,duplicate_count:1,reference_conflict_count:0,missing_reference_count:1,unavailable_count:0,items:cases.map((row,i)=>({test_run_item_id:row.id,row_number:i+1,case_name:row.case_name,category:['new','duplicate','missing_reference'][i]}))};};
methods.confirmTestImport=async(_,payload)=>{state.copyCount++;state.writes.push({name:'confirmTestImport',payload});return {dataset_id:dataset.id,working_revision:5,added:2,duplicates:1,conflicts:0,published:false};};
export const api=`);
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: 'jsx', resolveDir: fileURLToPath(new URL('../src', import.meta.url)) },
  plugins: [{ name: 'offline-api', setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: 'js' })); } }],
  bundle: true, write: false, outfile: join(output, 'bundle.js'), platform: 'browser', format: 'iife', jsx: 'automatic', define: { 'process.env.NODE_ENV': '"production"' }, loader: { '.md': 'text', '.woff2': 'dataurl' }, logLevel: 'silent' });
const archive = join(homedir(), '.cache/uv/archive-v0');
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, 'playwright/driver/package/index.mjs')).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), '.cache/ms-playwright/chromium-1208/chrome-linux64/chrome'), headless: true, args: ['--no-sandbox'] });
let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const errors = [], network = []; let screenshots = 0;
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => route.request().url().split('#')[0] === 'https://fixture.invalid/' ? route.fulfill({ contentType: 'text/html', body: '<html lang="ko"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' }) : (network.push(route.request().url()), route.abort()));
  await page.goto('https://fixture.invalid/');
  await page.addStyleTag({ content: bundle.outputFiles.find(f => f.path.endsWith('.css')).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(f => f.path.endsWith('.js')).text });
  page.setDefaultTimeout(10000);
  await page.locator('.r3-overview').waitFor();
  const datasetId = await page.evaluate(()=>fixture.datasetId), runId=await page.evaluate(()=>fixture.runs[0].id);
  const go=hash=>page.evaluate(hash=>{location.hash=hash;},hash);
  const snap=async name=>{await page.evaluate(async()=>{window.scrollTo({top:0,behavior:'instant'});await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,name);await page.screenshot({path:join(output,name+'.png'),fullPage:true});screenshots++;};
  const catalog=()=>page.locator('.evaluation-dataset-catalog');
  const showCatalog=async()=>{await go('#evaluate/ground-truth');await catalog().locator('tbody tr').first().waitFor();};
  for (const theme of ['SK','Light','Dark']) {
    await page.getByRole('button',{name:/^(테마|Theme)$/}).click();await page.getByRole('button',{name:theme,exact:true}).click();
    for (const locale of ['KR','EN']) {
      await page.getByRole('button',{name:locale,exact:true}).click();
      await go('#overview');await page.locator('.r3-overview').waitFor();
      await page.evaluate(()=>{fixture.draftReads=[];fixture.catalogReads=[];});
      await showCatalog();
      assert.equal(await catalog().locator('select[aria-label="평가 데이터셋"]').count(),0);
      assert.equal(await page.evaluate(()=>fixture.draftReads.length),0,'no N+1 detail reads');
      assert.equal(await page.evaluate(()=>fixture.historyReads),0,'uses current configuration, not evaluation history');
      assert.equal(await catalog().locator('tbody tr').count(),20);
      assert.match(await catalog().locator('tbody tr').first().innerText(),/회귀 검증 01/,'does not force current dataset to top');
      const activeRow=catalog().locator('tr').filter({hasText:'WAF 핵심 회귀셋'});
      assert.match(await activeRow.innerText(),/r4/);assert.match(await activeRow.innerText(),/r3/);
      const help=activeRow.getByRole('button',{name:locale==='KR'?'현재 운영 평가 설명':'Current Production Evaluation help'});
      await help.focus();await page.getByRole('tooltip').waitFor();assert.match(await page.getByRole('tooltip').innerText(),locale==='KR'?/현재 운영 설정과 동일한 구성/:/current Production configuration/);await page.keyboard.press('Escape');
      await snap('catalog-'+locale+'-'+theme);
      await activeRow.getByRole('button',{name:'WAF 핵심 회귀셋',exact:true}).focus();await page.keyboard.press('Enter');
      await page.locator('.dataset-detail-summary').waitFor();assert.equal(await page.evaluate(()=>location.hash),'#evaluate/ground-truth/'+datasetId);
      assert.equal(await page.locator('.r3-ground-truth .r3-dataset-toolbar select').count(),0);
      const header=page.locator('.dataset-detail-summary');assert.match(await header.innerText(),/r3/);assert.match(await header.innerText(),/r4/);
      await snap('detail-'+locale+'-'+theme);
      if (theme==='Light'&&locale==='KR') {await page.setViewportSize({width:390,height:844});await snap('detail-mobile');await page.setViewportSize({width:1440,height:1000});}
      await go('#evaluate/tests/'+runId);await page.locator('.test-run-detail').waitFor();
      await page.getByRole('button',{name:locale==='KR'?'테스트 작업':'Test actions',exact:true}).click();
      const copy=page.getByRole('button',{name:locale==='KR'?'테스트 문항을 평가 데이터셋에 복사':'Copy Test Cases to Evaluation Dataset',exact:true});assert.equal(await copy.isEnabled(),true);await copy.click();
      const dialog=page.getByRole('dialog');await dialog.getByRole('button',{name:locale==='KR'?'미리보기':'Preview',exact:true}).click();
      await dialog.locator('.r5-import-summary').waitFor();assert.deepEqual(await dialog.locator('.r5-import-summary strong').allTextContents(),['1','1','1','0','0']);
      await snap('copy-'+locale+'-'+theme);
      if (theme==='Light'&&locale==='KR') {await page.setViewportSize({width:390,height:844});await snap('copy-mobile');await page.setViewportSize({width:1440,height:1000});}
      const before=await page.evaluate(()=>fixture.copyCount);
      await dialog.getByRole('button',{name:locale==='KR'?'초안에 추가':'Add to Draft',exact:true}).evaluate(button=>{button.click();button.click();});
      await dialog.getByRole('button',{name:locale==='KR'?'평가 데이터셋 열기':'Open Evaluation Dataset',exact:true}).waitFor();assert.equal(await page.evaluate(()=>fixture.copyCount),before+1);
      await dialog.getByRole('button',{name:locale==='KR'?'평가 데이터셋 열기':'Open Evaluation Dataset',exact:true}).click();await page.locator('.dataset-detail-summary').waitFor();
    }
  }
  await page.getByRole('button',{name:'KR',exact:true}).click();
  await showCatalog();await catalog().getByRole('textbox',{name:'평가 데이터셋 검색',exact:true}).fill('회귀');
  await page.waitForFunction(()=>fixture.catalogReads.at(-1)?.query==='회귀');
  await catalog().getByRole('button',{name:'다음 페이지',exact:true}).click();await page.waitForFunction(()=>fixture.catalogReads.at(-1)?.offset===20);
  const row=catalog().locator('tbody tr').filter({hasText:'회귀 검증 21'});await row.locator('td').last().click();await page.locator('.dataset-detail-summary').waitFor();
  await page.locator('.dataset-detail-toolbar').getByRole('button',{name:'← 평가 데이터셋',exact:true}).click();await catalog().getByRole('button',{name:'회귀 검증 21',exact:true}).waitFor();
  assert.equal(await catalog().getByRole('textbox',{name:'평가 데이터셋 검색',exact:true}).inputValue(),'회귀');assert.equal(await page.evaluate(()=>location.hash),'#evaluate/ground-truth');
  await page.goForward();await page.locator('.dataset-detail-summary').waitFor();await page.goBack();await catalog().getByRole('button',{name:'회귀 검증 21',exact:true}).waitFor();
  await catalog().getByRole('textbox',{name:'평가 데이터셋 검색',exact:true}).fill('no-match');await catalog().getByText('검색 결과가 없습니다.',{exact:true}).waitFor();await snap('empty-search');
  await catalog().getByRole('textbox',{name:'평가 데이터셋 검색',exact:true}).fill('');await catalog().getByRole('button',{name:'WAF 핵심 회귀셋',exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});await snap('catalog-mobile');await page.setViewportSize({width:1440,height:1000});
  // An unconfigured Production has no invented badge, and read failures are explicit.
  await go('#overview');await page.evaluate(()=>{fixture.noEvaluation=true;});await showCatalog();assert.equal(await catalog().locator('.dataset-production-badge').count(),0);await snap('no-production-evaluation');
  await go('#overview');await page.evaluate(()=>{fixture.noEvaluation=false;fixture.productionFailure=true;fixture.catalogFailure=true;});await go('#evaluate/ground-truth');await catalog().getByRole('alert').waitFor();assert.doesNotMatch(await page.locator('main').innerText(),/PRIVATE_/);await snap('read-errors');
  await page.evaluate(()=>{fixture.productionFailure=false;fixture.catalogFailure=false;});await catalog().getByRole('button',{name:'다시 조회',exact:true}).click();await catalog().getByRole('button',{name:'WAF 핵심 회귀셋',exact:true}).waitFor();
  await go('#evaluate/tests/'+runId);await page.locator('.test-run-detail').waitFor();
  await page.evaluate(()=>{fixture.runs[0].accepting_items=true;});await page.getByRole('button',{name:'새로고침',exact:true}).click();
  await page.getByRole('button',{name:'테스트 작업',exact:true}).click();assert.equal(await page.getByRole('button',{name:'테스트 문항을 평가 데이터셋에 복사',exact:true}).isDisabled(),true);await page.keyboard.press('Escape');
  await page.evaluate(()=>{fixture.runs[0].accepting_items=false;fixture.runs[0].status='stopped';fixture.runs[0].stopped_at=new Date().toISOString();});await page.getByRole('button',{name:'새로고침',exact:true}).click();await page.locator('.test-stopped-notice').waitFor();
  await page.getByRole('button',{name:'테스트 작업',exact:true}).click();assert.equal(await page.getByRole('button',{name:'테스트 문항을 평가 데이터셋에 복사',exact:true}).isEnabled(),true);
  assert.deepEqual(network,[]);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({screenshots,checks:'catalog without N+1, r4 vs r3, current configuration source, no forced ordering, list/detail history, search/pagination restore, pending/stopped copy, open ingestion blocked, exclusive counts, duplicate submit guard, keyboard tooltip, SK/Light/Dark, KR/EN, mobile overflow, safe errors; no external traffic'}));
} catch(error) { if(page)await page.screenshot({path:join(output,'failure.png'),fullPage:true});throw error; }
finally { await browser.close(); }
