// Local browser test with in-bundle fixtures. No live API, DB or model calls.
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const source = fileURLToPath(new URL("../src", import.meta.url));
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
if (!driver) throw new Error("Existing development browser required; nothing is downloaded");
const { chromium } = await import(pathToFileURL(driver).href);
const mock = `
window.fixture={writes:[],reads:[],done:false,opened:[]};const state=window.fixture;
const summary=done=>({total:36,labeled:36,evaluable:done?36:0,matches:done?36:0,binary_decided:done?36:0,binary_evaluable:done?36:0,
 source_groups:[],outcomes:done?{match:36}:{failed:36},confusion_matrix:{tp:done?36:0},metrics:{accuracy:done?1:null}});
export const api={
 testEvaluations:async()=>({items:[{id:'saved-fixture',revision:1,created_at:'2026-09-16T00:00:00Z'}]}),
 testRun:async(id,query)=>{state.reads.push(query);const done=state.done&&!query.evaluation_id,pending=state.writes.length>0&&!done&&!query.evaluation_id;
 return {id,name:'재실행 검증',kind:'upload',created_at:'2026-09-16T00:00:00Z',status:done?'completed':pending?'pending':'failed',
 execution_mode:'moduagent',total:36,accepted:36,rejected:0,pending:pending?36:0,processing:0,completed:done?36:0,failed:!done&&!pending?36:0,
 total_elapsed_ms:1200,profile_metadata:{model_name:'가상 모델'},evaluation_summary:summary(done),total_items:36,
 facets:{difficulties:['hard'],test_categories:[]},items:[{id:'item-1',analysis_id:done?'retry-1':'original-1',original_analysis_id:'original-1',
 row_number:1,case_name:'문항 1',ingest_status:'accepted',status:done?'completed':pending?'pending':'failed',retry_count:state.writes.length?1:0,
 verdict:done?'true_positive':null,summary_ko:done?'원문 근거를 확인했습니다.':null,evaluation:{outcome:done?'match':pending?'pending':'failed',reference_label:{verdict:'true_positive',revision:1}}}]};},
 testRetryEligibility:async id=>({test_run_id:id,failed_count:36,eligible_count:36,eligible_ids:Array.from({length:36},(_,i)=>'original-'+i),blocked_counts:{},external_calls:false}),
 retryTestFailures:async(id,payload)=>{state.writes.push(payload);await new Promise(resolve=>setTimeout(resolve,60));return {test_run_id:id,enqueued:36,skipped:0,duplicate:false};}
};`;
const bundle = await build({ stdin: { contents: `import {useState} from 'react';import {createRoot} from 'react-dom/client';
import {TestRunDetail,initialTestRunFilters} from './TestRuns.jsx';
function Fixture(){const [filters,setFilters]=useState({...initialTestRunFilters(),difficulty:'hard',status:'failed',evaluation_id:'saved-fixture',offset:25});
return <TestRunDetail id="fixture-run" filters={filters} onFiltersChange={setFilters} onBack={()=>{}} onOpen={id=>window.fixture.opened.push(id)}/>;}
createRoot(document.getElementById('root')).render(<Fixture/>);`, loader: "jsx", resolveDir: source },
  plugins: [{ name: "offline-api", setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }],
  bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' },
  loader: { ".css": "empty", ".md": "text" }, logLevel: "silent" });
const css = ["styles.css", "ux.css", "testRuns.css", "validationData.css", "evaluationMetrics.css", "referenceLabels.css", "dataTable.css"].map(file => readFileSync(join(source, file), "utf8")).join("\n");
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
try {
  const page = await browser.newPage({ viewport: { width: 1360, height: 1000 } });
  const errors = []; let network = 0;
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => { network++; return route.abort(); });
  await page.setContent('<html lang="ko"><body><main id="root" style="padding:24px;max-width:1280px;margin:auto"></main></body></html>');
  await page.addStyleTag({ content: css }); await page.addScriptTag({ content: bundle.outputFiles[0].text });
  await page.getByRole("button", { name: "실패 항목 모두 재실행", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "실패 항목 모두 재실행", exact: true });
  const submit = dialog.getByRole("button", { name: "36건 재실행", exact: true });
  await submit.waitFor(); assert.equal(await submit.isDisabled(), true);
  assert.match(await dialog.innerText(), /현재 페이지와 필터에 관계없이/);
  await dialog.getByRole("checkbox").check();
  await submit.evaluate(button => { button.click(); button.click(); });
  await page.waitForFunction(() => window.fixture.reads.at(-1)?.reference_basis === 'latest' && document.querySelector('table.data-table'));
  const writes = await page.evaluate(() => window.fixture.writes);
  assert.equal(writes.length, 1); assert.equal(writes[0].analysis_ids.length, 36); assert.equal(writes[0].cost_acknowledged, true);
  const query = await page.evaluate(() => window.fixture.reads.at(-1));
  assert.equal(query.offset, 0); assert.equal(query.status, undefined); assert.equal(query.difficulty, undefined);
  await page.evaluate(() => { window.fixture.done = true; });
  await page.waitForFunction(() => document.querySelector('.quality-metric-accuracy > strong')?.textContent === '100.0%', { timeout: 15000 });
  const table = page.getByRole("table", { name: "문항별 분석 결과", exact: true });
  assert.match(await table.innerText(), /재실행 1회/); assert.match(await table.innerText(), /일치/);
  await table.getByRole("button", { name: "문항 1", exact: true }).click();
  assert.deepEqual(await page.evaluate(() => window.fixture.opened), ["retry-1"]);
  await page.getByRole("combobox", { name: "참고 답안 기준", exact: true }).selectOption("saved-fixture");
  await page.waitForFunction(() => document.querySelector('.quality-metric-accuracy > strong')?.textContent === '계산 불가');
  assert.match(await page.locator('.dataset-version-bar').innerText(), /저장 당시 결과/);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "실패 항목 모두 재실행", exact: true }).click();
  await dialog.waitFor(); assert.equal(await dialog.isVisible(), true);
  assert.equal(await dialog.evaluate(node => node.getBoundingClientRect().right <= innerWidth + 1), true);
  assert.deepEqual(errors, []); assert.equal(network, 0);
  console.log(JSON.stringify({ passed: ["consent", "all 36 targets beyond page", "double click suppression", "live filter reset", "automatic polling and metrics", "current detail navigation", "saved evaluation", "mobile dialog"], actualApiCalls: 0, llmCalls: 0 }));
} finally { await browser.close(); }
