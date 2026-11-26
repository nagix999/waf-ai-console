// Real React with offline, synthetic reads. No live service, DB or model calls.
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { r5BrowserFixture } from "./r5-browser-fixture.mjs";

const output = fileURLToPath(new URL("../../test-results/test-statistics-refresh", import.meta.url));
mkdirSync(output, { recursive: true });
const mock = r5BrowserFixture().replace("export const api=", `
state.progress=1;state.holdStatistics=false;state.releases=[];state.statisticsError=null;
const oldRun=methods.testRun;
methods.testRun=async which=>({...await oldRun(which),status:'processing',completed:state.progress,pending:29-state.progress,processing:1,total:30,accepted:30});
const stat=()=>({cases:30,completed:state.progress,failed:0,pending:29-state.progress,processing:1,canceled:0,true_positive:state.progress,false_positive:0,inconclusive:0,distribution_total:state.progress,verdict_rates:{true_positive:1,false_positive:0,inconclusive:0},comparable:state.progress,labeled:30,matches:state.progress,accuracy:1,false_negatives:0,false_positives:0});
methods.testStatistics=async(which,query)=>{if(state.holdStatistics)await new Promise(resolve=>state.releases.push(resolve));if(state.statisticsError)throw Object.assign(Error('PRIVATE_ERROR'),{status:state.statisticsError});return {test_run_id:which,summary:stat(),items:Array.from({length:10},(_,n)=>({...stat(),value:'검토용 그룹 '+(n+1),values:{[query.group_by]:'검토용 그룹 '+(n+1)}})),total_groups:30,reference_basis:'ground_truth',rejected:0,duplicates:0}};
const duration=n=>({recorded:state.progress,unrecorded:0,mean_ms:n,p50_ms:n,p95_ms:n*1.4,p99_ms:n*1.8,min_ms:n/2,max_ms:n*2});
methods.testPerformance=async which=>({version:'test-performance-summary-v1',test_run_id:which,cases:30,completed_cases:state.progress,statuses:{completed:state.progress,pending:29-state.progress,processing:1},timings:{analysis_queue_ms:duration(1000),analysis_processing_ms:duration(9000),decision_elapsed_ms:duration(10000),result_ready_elapsed_ms:duration(12000)},completed_timings:{analysis_queue_ms:duration(1000),analysis_processing_ms:duration(9000),decision_elapsed_ms:duration(10000),result_ready_elapsed_ms:duration(12000)},phases:{},roles:{},display:{states:{completed:state.progress},no_job_cases:0,queue_ms:duration(200),processing_ms:duration(1000)}});
export const api=`);
const bundle = await build({ stdin: { contents: `import {useState} from 'react';import {createRoot} from 'react-dom/client';import {TestRunDetail,initialTestRunFilters} from './TestRuns.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';import './workspacePolish.css';function Harness(){const[filters,setFilters]=useState(()=>({...initialTestRunFilters(),view:'statistics'}));return <TestRunDetail id="00000008-1111-4111-8111-111111111111" filters={filters} onFiltersChange={setFilters} onBack={()=>{}} onOpen={()=>{}}/>;}createRoot(document.getElementById('root')).render(<Harness/>);`, loader: "jsx", resolveDir: fileURLToPath(new URL("../src", import.meta.url)) }, plugins: [{ name: "offline-api", setup(builder) { builder.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }], bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
const errors = [], traffic = [];
let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 960 }, reducedMotion: "reduce" });
  page.setDefaultTimeout(10000);
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => route.request().url() === "https://fixture.invalid/" ? route.fulfill({ contentType: "text/html", body: '<html lang="ko"><body><main id="root"></main></body></html>' }) : (traffic.push(route.request().url()), route.abort()));
  await page.goto("https://fixture.invalid/");
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  await page.locator(".test-stat-table .tabulator-row").first().waitFor();
  await page.locator(".timing-summary-cards > div").first().waitFor();
  await page.getByRole("button", { name: "조건 편집", exact: true }).click();
  await page.locator(".test-stat-search input").fill("아직 적용하지 않은 검색");
  await page.locator(".oe-stat-definition > summary").click();
  await page.locator(".oe-statistics-more > summary").click();
  await page.locator(".oe-performance-details > summary").click();
  await page.evaluate(() => {
    const input = document.querySelector(".test-stat-search input");
    window.savedNodes = { statistics: document.querySelector(".test-statistics"), table: document.querySelector(".test-stat-table"), input };
    fixture.holdStatistics = true; fixture.progress = 2;
    scrollTo(0, document.documentElement.scrollHeight - innerHeight - 120);
    input.focus({ preventScroll: true });
    window.savedScroll = scrollY;
  });
  const firstReads = await page.evaluate(() => fixture.reads.filter(call => call.name === "testStatistics").length);
  await page.waitForFunction(firstReads => fixture.reads.filter(call => call.name === "testStatistics").length > firstReads, firstReads);
  await page.waitForFunction(() => fixture.releases.length > 0);
  assert.equal(await page.locator(".test-statistics").getAttribute("aria-busy"), "false", "background polling does not turn the page into a loading screen");
  const retained = await page.evaluate(() => ({ mounted: Object.values(savedNodes).every(node => node.isConnected), focused: document.activeElement === savedNodes.input, draft: savedNodes.input.value, open: [".oe-stat-definition", ".oe-statistics-more", ".oe-performance-details"].every(selector => document.querySelector(selector)?.open), scrollDelta: Math.abs(scrollY - savedScroll) }));
  assert.deepEqual(retained, { mounted: true, focused: true, draft: "아직 적용하지 않은 검색", open: true, scrollDelta: 0 });
  await page.evaluate(() => { fixture.holdStatistics = false; fixture.releases.splice(0).forEach(resolve => resolve()); });
  await page.waitForFunction(() => document.querySelector(".test-statistics").getAttribute("aria-busy") === "false");
  assert.equal(await page.evaluate(() => document.activeElement === savedNodes.input), true);
  assert.equal(await page.evaluate(() => Math.abs(scrollY - savedScroll) <= 2), true, "data-only updates preserve document scroll");
  await page.screenshot({ path: join(output, "statistics-refreshed.png"), fullPage: true });

  // A new grouping must never display old grouping rows while it is loading.
  await page.evaluate(() => { fixture.holdStatistics = true; });
  await page.getByRole("combobox", { name: "집계 기준", exact: true }).selectOption("company_name");
  await page.waitForFunction(() => !document.querySelector(".test-stat-table"));
  assert.equal(await page.getByText("통계를 불러오는 중…", { exact: true }).count(), 1);
  await page.waitForFunction(() => fixture.releases.length > 0);
  await page.evaluate(() => { fixture.holdStatistics = false; fixture.releases.splice(0).forEach(resolve => resolve()); });
  await page.locator(".test-stat-table .tabulator-row").first().waitFor();

  // Authorization errors clear previously visible statistics rather than
  // retaining protected stale content under an error message.
  await page.evaluate(() => { fixture.statisticsError = 403; });
  await page.locator(".oe-stat-group-panel").getByRole("button", { name: "다시 조회", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "통계 정보를 조회하지 못했습니다" }).waitFor();
  assert.equal(await page.locator(".test-stat-table").count(), 0);
  assert.deepEqual(errors, []); assert.deepEqual(traffic, []);
  assert.equal(await page.evaluate(() => fixture.writes.length), 0);
  console.log(JSON.stringify({ checks: ["ongoing polling", "same-scope retained nodes", "draft and focus", "expanded details", "document scroll", "scope reset", "403 clears prior data", "no network or writes"], retained, output }));
} finally {
  writeFileSync(join(output, "audit.json"), JSON.stringify({ errors, traffic }, null, 2));
  await browser.close();
}
