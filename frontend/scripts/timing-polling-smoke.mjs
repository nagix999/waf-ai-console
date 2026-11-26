// Synthetic reads only. Exercises independently timed parent/child polling.
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { r5BrowserFixture } from "./r5-browser-fixture.mjs";

const output = fileURLToPath(new URL("../../test-results/timing-polling", import.meta.url));
mkdirSync(output, { recursive: true });
const mock = r5BrowserFixture().replace("export const api=", `
state.progress=1;state.ingesting=false;state.terminal=false;state.runReads=0;
state.pollReads=[];state.performanceReads=[];state.productionReads=[];state.productionError=0;
const oldRun=methods.testRun;
methods.testRun=async which=>{if(state.runReads++)await new Promise(resolve=>setTimeout(resolve,700));if(!state.terminal&&!state.ingesting)state.progress++;return {...await oldRun(which),status:state.terminal?'completed':state.ingesting?'completed':'processing',accepting_items:state.ingesting,can_stop:!state.terminal,completed:state.progress,pending:state.ingesting||state.terminal?0:29-state.progress,processing:state.ingesting||state.terminal?0:1,total:30,accepted:30};};
const stat=()=>({cases:30,completed:state.progress,failed:0,pending:state.ingesting||state.terminal?0:29-state.progress,processing:state.ingesting||state.terminal?0:1,canceled:0,true_positive:state.progress,false_positive:0,inconclusive:0,distribution_total:state.progress,verdict_rates:{true_positive:1,false_positive:0,inconclusive:0},comparable:state.progress,labeled:30,matches:state.progress,accuracy:1,false_negatives:0,false_positives:0});
methods.testStatistics=async(which,query)=>{state.pollReads.push(Date.now());return {test_run_id:which,summary:stat(),items:[{...stat(),value:'검증용 그룹',values:{[query.group_by]:'검증용 그룹'}}],total_groups:1,reference_basis:'ground_truth',rejected:0,duplicates:0};};
const duration=n=>({recorded:state.progress,unrecorded:0,mean_ms:n,p50_ms:n,p95_ms:n*1.4,p99_ms:n*1.8,min_ms:n/2,max_ms:n*2});
const timing=()=>Object.fromEntries(['analysis_queue_ms','analysis_processing_ms','decision_elapsed_ms','result_ready_elapsed_ms'].map(key=>[key,duration(1000)]));
methods.testPerformance=async which=>{state.performanceReads.push(Date.now());return {version:'test-performance-summary-v1',test_run_id:which,cases:30,completed_cases:state.progress,statuses:stat(),timings:timing(),completed_timings:timing(),phases:{},roles:{},display:{states:{completed:state.progress},no_job_cases:0}};};
methods.analysisPerformance=async()=>{state.productionReads.push(Date.now());if(state.productionError)throw Object.assign(Error('PRIVATE_ERROR'),{status:state.productionError});return {version:'analysis-performance-summary-v1',cases:30,completed_cases:state.progress,statuses:{completed:state.progress,processing:1},completed_timings:timing()};};
export const api=`);
const bundle = await build({ stdin: { contents: `import {useState} from 'react';import {createRoot} from 'react-dom/client';import {TestRunDetail,initialTestRunFilters} from './TestRuns.jsx';import ProductionPerformanceSummary from './ProductionPerformanceSummary.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';import './workspacePolish.css';function Harness(){const[filters,setFilters]=useState(()=>({...initialTestRunFilters(),view:'statistics'}));return <><TestRunDetail id="00000008-1111-4111-8111-111111111111" filters={filters} onFiltersChange={setFilters} onBack={()=>{}} onOpen={()=>{}}/><ProductionPerformanceSummary filters={{analysis_purpose:'production'}}/></>;}createRoot(document.getElementById('root')).render(<Harness/>);`, loader: "jsx", resolveDir: fileURLToPath(new URL("../src", import.meta.url)) }, plugins: [{ name: "offline-api", setup(builder) { builder.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }], bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
const errors = [], traffic = [];
let report = {};
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, reducedMotion: "reduce" });
  page.setDefaultTimeout(10000);
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => route.request().url() === "https://fixture.invalid/" ? route.fulfill({ contentType: "text/html", body: '<html lang="ko"><body><main id="root"></main></body></html>' }) : (traffic.push(route.request().url()), route.abort()));
  await page.goto("https://fixture.invalid/");
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  await page.locator(".test-stat-table .tabulator-row").first().waitFor();
  await page.waitForTimeout(6600);
  report.initial = await page.evaluate(() => ({ run: fixture.runReads, statistics: fixture.pollReads.length, performance: fixture.performanceReads.length, production: fixture.productionReads.length }));
  assert.deepEqual(report.initial, { run: 2, statistics: 2, performance: 2, production: 1 }, "parent counters do not trigger duplicate summary reads; collapsed Production reads once");

  const production = page.locator(".production-performance-summary");
  await production.locator("details > summary").click();
  await page.waitForFunction(() => fixture.productionReads.length === 2);
  await page.waitForTimeout(5250);
  assert.equal(await page.evaluate(() => fixture.productionReads.length), 3, "expanded active Production polls");
  await production.locator("details > summary").click();
  await page.waitForTimeout(5250);
  assert.equal(await page.evaluate(() => fixture.productionReads.length), 3, "closing Production stops polling");

  // No matching active cases does not mean an API-ingested Test has finished.
  await page.evaluate(() => { fixture.ingesting = true; });
  await page.waitForTimeout(6100);
  const ingestionStart = await page.evaluate(() => ({ statistics: fixture.pollReads.length, performance: fixture.performanceReads.length }));
  await page.waitForTimeout(5250);
  report.ingestion = await page.evaluate(() => ({ statistics: fixture.pollReads.length, performance: fixture.performanceReads.length }));
  assert.ok(report.ingestion.statistics > ingestionStart.statistics, "open ingestion keeps group summary current");
  assert.ok(report.ingestion.performance > ingestionStart.performance, "open ingestion keeps timing summary current");

  // A terminal parent transition must read final values, then stop both loops.
  await page.evaluate(() => { fixture.ingesting = false; fixture.terminal = true; fixture.progress = 30; });
  await page.waitForFunction(() => document.querySelector(".oe-test-inline-totals dd")?.textContent === "30 / 30");
  await page.waitForFunction(() => document.querySelector(".test-stat-overview strong")?.textContent === "30");
  await page.waitForTimeout(500);
  const terminalStart = await page.evaluate(() => ({ run: fixture.runReads, statistics: fixture.pollReads.length, performance: fixture.performanceReads.length }));
  await page.waitForTimeout(5600);
  report.terminal = await page.evaluate(() => ({ run: fixture.runReads, statistics: fixture.pollReads.length, performance: fixture.performanceReads.length }));
  assert.deepEqual(report.terminal, terminalStart, "terminal Test does a final read then stops polling");

  await page.evaluate(() => { fixture.productionError = 403; });
  await production.locator("details > summary").click();
  await production.getByRole("alert").waitFor();
  assert.equal(await production.locator(".timing-summary-cards").count(), 0, "permission errors clear retained Production measurements");
  assert.deepEqual(errors, []); assert.deepEqual(traffic, []);
  assert.equal(await page.evaluate(() => fixture.writes.length), 0);
  console.log(JSON.stringify({ status: "passed", ...report, checks: ["independent parent/child schedules", "no counter-triggered duplicate summaries", "collapsed Production one-shot only", "expanded Production polling", "open ingestion with no active matching cases", "terminal final read and cleanup", "403 clears retained data", "no network/models/writes"] }));
} finally {
  writeFileSync(join(output, "audit.json"), JSON.stringify({ ...report, errors, traffic }, null, 2));
  await browser.close();
}
