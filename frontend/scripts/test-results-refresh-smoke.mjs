// Actual React/Tabulator with synthetic reads only. No service, DB or model.
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { r5BrowserFixture } from "./r5-browser-fixture.mjs";

const output = fileURLToPath(new URL("../../test-results/test-results-refresh", import.meta.url));
mkdirSync(output, { recursive: true });
const mock = r5BrowserFixture().replace("export const api=", `
Object.assign(state,{revision:1,terminal:false,holdReads:false,releases:[],readError:null});
const waitForRead=async()=>{if(state.holdReads)await new Promise(resolve=>state.releases.push(resolve));if(state.readError)throw Object.assign(Error('PRIVATE_ERROR_MUST_NOT_RENDER'),{status:state.readError});};
const fixtureRun=(which,n=0)=>({...runs[0],id:which,name:'자동 갱신 테스트 '+(n+1),kind:'dataset',status:state.terminal?'failed':'processing',accepting_items:false,can_stop:!state.terminal,official_evaluation_pending:false,canceled:0,duplicates:0,total:30,accepted:30,rejected:0,completed:state.terminal?29:state.revision,failed:1,pending:state.terminal?0:28-state.revision,processing:state.terminal?0:1,total_elapsed_ms:1000*state.revision});
const fixtureCases=()=>Array.from({length:25},(_,n)=>({...cases[n%3],id:id(200+n),analysis_id:id(300+n),case_name:'회귀 문항 '+(n+1),row_number:n+1,status:n===1?'failed':'completed',error_code:n===1?'primary_agent_failed':null,summary_ko:'원문 분석 갱신 '+state.revision+' · 문항 '+(n+1)}));
methods.testRuns=async query=>{await waitForRead();return {items:Array.from({length:10},(_,n)=>({...fixtureRun(n===0?id(8):id(100+n),n),...(query.q?{name:'범위 변경 테스트 '+(n+1)}:{})})),total:10};};
methods.testRun=async which=>{await waitForRead();return {...fixtureRun(which),items:fixtureCases(),total_items:30,facets:{difficulties:['hard'],test_categories:['SQLi']}};};
const originalPreflight=methods.promotionPreflight;
methods.promotionPreflight=async which=>{const result=await originalPreflight(which);return {...result,eligible:false,candidate_name:'실패 문항이 포함된 테스트',checks:result.checks.map(check=>({...check,passed:check.code!=='candidate_completed_without_failures'}))};};
export const api=`);
const bundle = await build({
  stdin: { contents: `
import {useState} from 'react';import {createRoot} from 'react-dom/client';
import {TestRunHistory,TestRunDetail,initialTestRunHistoryState,initialTestRunFilters} from './TestRuns.jsx';
import {ConsolePreferencesProvider} from './consolePreferences.jsx';import {Promotion} from './LifecycleViews.jsx';
import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';import './workspacePolish.css';
function Harness(){const[mode,setMode]=useState('history'),[history,setHistory]=useState(initialTestRunHistoryState),[filters,setFilters]=useState(initialTestRunFilters);window.testHarness={setMode,setHistory,setFilters};return <ConsolePreferencesProvider><main style={{padding:24}}>{mode==='history'?<TestRunHistory state={history} onStateChange={setHistory} onSelect={()=>{}}/>:mode==='detail'?<TestRunDetail id="00000008-1111-4111-8111-111111111111" filters={filters} onFiltersChange={setFilters} onBack={()=>setMode('history')} onOpen={()=>{}} onPromote={()=>setMode('promotion')}/>:<Promotion initialRunId="00000008-1111-4111-8111-111111111111" onNavigate={()=>{}} onBack={()=>setMode('detail')}/>}<div aria-hidden="true" style={{height:900}}/></main></ConsolePreferencesProvider>;}createRoot(document.getElementById('root')).render(<Harness/>);`, loader: "jsx", resolveDir: fileURLToPath(new URL("../src", import.meta.url)) },
  plugins: [{ name: "offline-api", setup(builder) { builder.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }],
  bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent",
});
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
if (!driver) throw Error("An existing browser is required; no download is attempted.");
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
const errors = [], traffic = [], checks = [];
let page;

async function loaded(mode) {
  const selector = mode === "history" ? ".test-run-history" : ".test-run-detail";
  await page.locator(`${selector} .tabulator-row`).first().waitFor();
  await page.waitForFunction(selector => document.querySelector(selector)?.getAttribute("aria-busy") === "false", selector);
  return page.locator(selector);
}
async function capture(mode, focus = "cell") {
  return page.evaluate(async ({mode,focus}) => {
    const container=document.querySelector(mode==='history'?'.test-run-history':'.test-run-detail');
    const table=container.querySelector('.tabulator'),holder=table.querySelector('.tabulator-tableholder');
    const row=table.querySelector('.tabulator-row'),cell=row.querySelector(mode==='history'?'[tabulator-field="name"]':'[tabulator-field="summary"]');
    const control=focus==='draft'?container.querySelector('.test-run-search input'):row.querySelector('button');
    const details=container.querySelector(mode==='history'?'.oe-test-table-context':'.oe-run-configuration');
    details.open=true;
    const draft=container.querySelector('.test-run-search input');
    holder.style.setProperty('height','260px','important');holder.style.setProperty('max-height','260px','important');
    table.querySelector('.tabulator-table').style.minWidth='1800px';
    control.focus({preventScroll:true});
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    holder.scrollTop=80;holder.scrollLeft=90;scrollTo(0,180);
    window.saved={mode,container,table,holder,row,cell,control,details,draft,scrollY,scrollTop:holder.scrollTop,scrollLeft:holder.scrollLeft,removed:false,geometry:{tableTop:table.getBoundingClientRect().top+scrollY,rowHeight:row.getBoundingClientRect().height,containerHeight:container.getBoundingClientRect().height,documentHeight:document.documentElement.scrollHeight}};
    window.refreshObserver?.disconnect();window.refreshObserver=new MutationObserver(()=>{if([table,row,cell,control].some(node=>!node.isConnected))saved.removed=true;});
    refreshObserver.observe(container,{childList:true,subtree:true});
    return {scrollY,scrollTop:holder.scrollTop,scrollLeft:holder.scrollLeft};
  }, {mode,focus});
}
async function retained(mode) {
  const actual = await page.evaluate(() => ({
    mounted:[saved.container,saved.table,saved.row,saved.cell,saved.control,saved.details].every(node=>node.isConnected)&&!saved.removed,
    focused:document.activeElement===saved.control,open:saved.details.open,
    scroll:Math.abs(scrollY-saved.scrollY)<=2&&Math.abs(saved.holder.scrollTop-saved.scrollTop)<=2&&Math.abs(saved.holder.scrollLeft-saved.scrollLeft)<=2,
    draft:saved.draft?.value??null,selected:fixture.writes.length===0&&(saved.mode!=='detail'||document.querySelector('input[aria-label="회귀 문항 1 선택"]')?.checked===true),
  }));
  const positions=await page.evaluate(()=>({current:{window:scrollY,top:saved.holder.scrollTop,left:saved.holder.scrollLeft,geometry:{tableTop:saved.table.getBoundingClientRect().top+scrollY,rowHeight:saved.row.getBoundingClientRect().height,containerHeight:saved.container.getBoundingClientRect().height,documentHeight:document.documentElement.scrollHeight}},expected:{window:saved.scrollY,top:saved.scrollTop,left:saved.scrollLeft,geometry:saved.geometry}}));
  assert.deepEqual(actual,{mounted:true,focused:true,open:true,scroll:true,draft:mode==='history'?'적용 전 검색 초안':null,selected:true},JSON.stringify(positions));
  return actual;
}
async function release() {
  await page.evaluate(() => {fixture.holdReads=false;fixture.releases.splice(0).forEach(resolve=>resolve());});
}
async function screenshot(name) {
  const previous=await page.evaluate(()=>scrollY);
  await page.evaluate(()=>scrollTo(0,0));
  await page.screenshot({path:join(output,name),fullPage:false});
  await page.evaluate(previous=>scrollTo(0,previous),previous);
}
async function refresh(mode, polling) {
  const method=mode==='history'?'testRuns':'testRun',container=page.locator(mode==='history'?'.test-run-history':'.test-run-detail');
  const previous=await page.evaluate(method=>fixture.reads.filter(call=>call.name===method).length,method);
  await page.evaluate(()=>{fixture.holdReads=true;fixture.revision+=1;});
  if(!polling)await container.getByRole('button',{name:'새로고침',exact:true}).evaluate(node=>node.click());
  await page.waitForFunction(({method,previous})=>fixture.reads.filter(call=>call.name===method).length>previous,{method,previous});
  await page.waitForFunction(()=>fixture.releases.length>0);
  await retained(mode);
  assert.equal(await container.getByText(mode==='history'?'테스트 목록을 불러오는 중…':'실행 정보를 불러오는 중…',{exact:true}).count(),0,'same-scope refresh keeps visible content');
  await release();
  await page.waitForFunction(mode=>{const row=document.querySelector((mode==='history'?'.test-run-history':'.test-run-detail')+' .tabulator-row');return mode==='history'?row?.textContent.includes(fixture.revision+' / 30'):row?.textContent.includes('원문 분석 갱신 '+fixture.revision);},mode);
  await retained(mode);
  checks.push(`${mode}: ${polling?'polling':'manual'} data update with retained DOM, focus and scroll`);
}

try {
  page=await browser.newPage({viewport:{width:1440,height:960},reducedMotion:'reduce'});page.setDefaultTimeout(12000);
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>route.request().url()==='https://fixture.invalid/'?route.fulfill({contentType:'text/html',body:'<html lang="ko"><body><div id="root"></div></body></html>'}):(traffic.push(route.request().url()),route.abort()));
  await page.goto('https://fixture.invalid/');
  await page.addStyleTag({content:bundle.outputFiles.find(file=>file.path.endsWith('.css')).text+'\n.test-run-history .tabulator-tableholder,.test-run-cases .tabulator-tableholder{max-height:360px;overflow:auto}.tabulator-table{min-width:1600px}'});
  await page.addScriptTag({content:bundle.outputFiles.find(file=>file.path.endsWith('.js')).text});
  await loaded('history');
  await page.locator('.test-run-search input').fill('적용 전 검색 초안');
  const positions=await capture('history','draft');
  assert.ok(positions.scrollY>0&&positions.scrollTop>0&&positions.scrollLeft>0,`exercise document and both table scroll directions: ${JSON.stringify(positions)}`);
  await refresh('history',true);
  await capture('history');await refresh('history',false);
  await screenshot('history-data-refresh.png');

  // New scope removes old rows while pending; it cannot show stale results.
  await page.evaluate(()=>{fixture.holdReads=true;testHarness.setHistory(current=>({...current,query:{...current.query,q:'범위 변경'}}));});
  await page.waitForFunction(()=>!document.querySelector('.test-run-history .tabulator'));
  assert.equal(await page.getByText('테스트 목록을 불러오는 중…',{exact:true}).count(),1);
  await page.waitForFunction(()=>fixture.releases.length>0);await release();await loaded('history');
  await page.getByRole('button',{name:'범위 변경 테스트 1',exact:true}).waitFor();checks.push('history: scope reset');

  // A transport failure retains stale same-scope data with an explicit error;
  // authorization and deletion errors must remove it instead.
  await page.evaluate(()=>{fixture.readError=503;});
  await page.locator('.test-run-history').getByRole('button',{name:'새로고침',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'목록을 갱신하지 못해 이전 값을 표시합니다'}).waitFor();
  assert.equal(await page.locator('.test-run-history .tabulator').count(),1);
  await page.evaluate(()=>{fixture.readError=null;});
  await page.locator('.test-run-history').getByRole('button',{name:'새로고침',exact:true}).click();await loaded('history');
  checks.push('history: transient read error preserves table');
  for(const status of [401,403,404]){
    await page.evaluate(status=>{fixture.readError=status;},status);
    await page.locator('.test-run-history').getByRole('button',{name:'새로고침',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('.test-run-history .tabulator'));
    assert.equal(await page.getByText('PRIVATE_ERROR_MUST_NOT_RENDER',{exact:true}).count(),0);
    await page.evaluate(()=>{fixture.readError=null;});
    await page.locator('.test-run-history').getByRole('button',{name:'새로고침',exact:true}).click();await loaded('history');
    checks.push(`history: ${status} clears protected stale data`);
  }
  await page.evaluate(()=>testHarness.setMode('detail'));await loaded('detail');
  await page.getByRole('checkbox',{name:'회귀 문항 1 선택',exact:true}).check();
  await capture('detail');await refresh('detail',true);await refresh('detail',false);
  await screenshot('cases-data-refresh.png');

  await page.evaluate(()=>{fixture.holdReads=true;testHarness.setFilters(current=>({...current,difficulty:'hard'}));});
  await page.waitForFunction(()=>!document.querySelector('.test-run-detail .tabulator'));
  assert.equal(await page.getByText('실행 정보를 불러오는 중…',{exact:true}).count(),1);
  await page.waitForFunction(()=>fixture.releases.length>0);await release();await loaded('detail');
  assert.equal(await page.getByRole('checkbox',{name:'회귀 문항 1 선택',exact:true}).isChecked(),false,'selection never crosses a changed filter');
  checks.push('detail: scope reset and selection isolation');
  for(const status of [401,403,404]){
    await page.evaluate(status=>{fixture.readError=status;},status);
    await page.locator('.test-run-detail').getByRole('button',{name:'새로고침',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('.test-run-detail .tabulator'));
    await page.evaluate(()=>{fixture.readError=null;});
    await page.locator('.test-run-detail').getByRole('button',{name:'새로고침',exact:true}).click();await loaded('detail');
    checks.push(`detail: ${status} clears protected stale data`);
  }
  // Processing lifecycle is complete despite a failed case. The underlying
  // failure, failure count, retry action and Production safety gate stay intact.
  await page.evaluate(()=>{fixture.terminal=true;});
  await page.locator('.test-run-detail').getByRole('button',{name:'새로고침',exact:true}).click();await loaded('detail');
  assert.equal(await page.locator('.oe-test-context-strip .status').innerText(),'처리 완료');
  assert.equal(await page.locator('.oe-run-failures dd').innerText(),'1');
  assert.equal(await page.getByRole('button',{name:'실패 항목 모두 재실행',exact:true}).count(),1);
  assert.match(await page.locator('.test-run-cases .tabulator-row').nth(1).innerText(),/실행 실패/);
  await screenshot('cases-terminal-with-failure.png');
  await page.evaluate(()=>testHarness.setMode('history'));await loaded('history');
  assert.equal(await page.locator('.test-run-history .tabulator-row').first().locator('.status').innerText(),'처리 완료');
  assert.match(await page.locator('.test-run-history .tabulator-row').first().innerText(),/실패 1/);
  await screenshot('history-terminal-with-failure.png');
  await page.evaluate(()=>testHarness.setMode('promotion'));
  await page.getByRole('button',{name:'운영에 반영',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'운영에 반영',exact:true}).isDisabled(),true);
  assert.equal(await page.evaluate(()=>fixture.terminal),true);
  checks.push('terminal batches: completed badge, failed cases/counts and retries retained, promotion blocked');
  assert.deepEqual(errors,[]);assert.deepEqual(traffic,[]);
  assert.deepEqual(await page.evaluate(()=>fixture.unknown),[]);
  assert.equal(await page.evaluate(()=>fixture.writes.length),0);
  checks.push('no network, writes or model calls');
  console.log(JSON.stringify({checks,output}));
} finally {
  if(page)await page.screenshot({path:join(output,'last-screen.png'),fullPage:true}).catch(()=>{});
  writeFileSync(join(output,'audit.json'),JSON.stringify({errors,traffic,checks},null,2));
  await browser.close();
}
