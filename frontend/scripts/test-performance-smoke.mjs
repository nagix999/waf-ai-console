// Full React app, synthetic telemetry and blocked external network only.
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { r5BrowserFixture } from "./r5-browser-fixture.mjs";

const output = fileURLToPath(new URL("../../test-results/test-performance", import.meta.url));
mkdirSync(output, { recursive: true });
const mock = r5BrowserFixture().replace("export const api=", `
state.runs=runs;state.performanceReads=[];state.statisticsReads=[];state.performanceMode='normal';state.performanceHold=false;
Object.assign(runs[0],{name:'처리 성능 검증',total:30,accepted:30,completed:30,status:'completed',failed:0,pending:0,processing:0,evaluation_mode:'reference',ground_truth:null,test_purpose:'development'});
const performanceMetric=(mean,count=30)=>({recorded:count,unrecorded:30-count,reported_sum_ms:mean*count,min_ms:mean/2,p50_ms:mean,p95_ms:mean*2,max_ms:mean*3,mean_ms:mean});
const performanceTokens=(mean)=>({recorded:30,unrecorded:0,sum:mean*30,mean,p50:mean,p95:mean*2,max:mean*3});
const performanceRole=(verifier=false)=>({not_observed_cases:verifier?10:0,accounting:{recorded_cases:verifier?20:30,unrecorded_cases:0,reported_output_attempts:40,reported_output_retries:10,reported_citation_repairs:0,cases_with_output_retries:10,cases_with_citation_repairs:0},first_attempt_ms:performanceMetric(verifier?18000:25000),invocation_ms:performanceMetric(40000),output_retry_ms:performanceMetric(10000),citation_repair_ms:performanceMetric(0),slot_wait_ms:performanceMetric(3000),tokens:{input_tokens:performanceTokens(4200),output_tokens:performanceTokens(900),first_attempt_output_tokens:performanceTokens(450)},output_failures:{stage_states:{completed:30},recorded_cases:30,unrecorded_cases:0,attempt_outcomes:{completed:30,output_validation:10},cases_with_output_failure:10,completed_cases_with_output_failure:10,validation_attempts_without_recorded_issues:0,validation_issues:[{field:'evidence',type:'evidence_source_unavailable',attempts:6},{field:'evidence',type:'decisive_verdict_requires_directional_content',attempts:4}]}});
methods.testPerformance=async(value,query,options={})=>{state.performanceReads.push({id:value,query});const mode=state.performanceMode;if(state.performanceHold){state.performanceHold=false;await new Promise(resolve=>{state.releasePerformance=resolve;});}if(mode==='error')throw Object.assign(Error('SYNTHETIC_PRIVATE_ERROR'),{status:503});const cases=query.case_filter?5:30;const summary={version:'test-performance-summary-v1',test_run_id:value,cases,statuses:{completed:cases},timings:{decision_elapsed_ms:performanceMetric(65000),result_ready_elapsed_ms:performanceMetric(80000),analysis_queue_ms:performanceMetric(5000),analysis_processing_ms:performanceMetric(60000)},phases:Object.fromEntries(['llm_primary','llm_verifier'].map(type=>[type,{entered_cases:30,not_observed_cases:0,states:{completed:30},duration:performanceMetric(40000)}])),roles:{primary:performanceRole(),verifier:performanceRole(true),legacy_evidence_editor:performanceRole()},display:{no_job_cases:0,states:{completed:cases},dispatch_markers:{true:cases},queue_ms:performanceMetric(4000),processing_ms:performanceMetric(11000),post_decision_elapsed_ms:performanceMetric(15000)}};if(mode==='missing'){summary.timings={};summary.roles={};summary.display={};}return summary;};
const performanceCounts=n=>({cases:n,completed:n,failed:0,pending:0,processing:0,canceled:0,true_positive:n,false_positive:0,inconclusive:0,distribution_total:n,comparable:n,matches:n,labeled:n,accuracy:1,label_coverage:1,false_negatives:0,false_positives:0,abstained:0,expected_hold_mismatches:0,unclassified_completed:0,verdict_rates:{true_positive:1,false_positive:0,inconclusive:0}});
methods.testStatistics=async(value,query)=>{state.statisticsReads.push(query);const fields=query.group_by.split(','),count=query.case_filter?5:30;const rows=Array.from({length:count},(_,index)=>({value:'시그니처 '+(index+1),values:Object.fromEntries(fields.map(field=>[field,'항목 '+(index+1)])),...performanceCounts(1)}));const result={test_run_id:value,group_by:query.group_by,group_fields:fields,items:rows.slice(Number(query.offset||0),Number(query.offset||0)+Number(query.limit||10)),total_groups:count,limit:Number(query.limit||10),offset:Number(query.offset||0),summary:performanceCounts(count),scope_cases:30,case_filter_applied:Boolean(query.case_filter),rejected:0,duplicates:0,reference_basis:'latest'};return result;};
export const api=`);
const bundle = await build({
  entryPoints: [fileURLToPath(new URL("../src/main.jsx", import.meta.url))],
  plugins: [{ name: "offline-performance", setup(plugin) {
    plugin.onResolve({ filter: /(?:^|\/)api\.js$/ }, () => ({ path: "api", namespace: "fixture" }));
    plugin.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({ contents: mock, loader: "js" }));
  } }], bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife",
  jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent",
});
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  const errors = [], network = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => {
    const url = route.request().url();
    if (url.split("#")[0] === "https://fixture.invalid/") return route.fulfill({ contentType: "text/html", body: '<html lang="ko"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' });
    network.push(url); return route.abort();
  });
  await page.goto("https://fixture.invalid/");
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  page.setDefaultTimeout(10000);
  await page.locator(".r3-overview").waitFor();
  const runId = await page.evaluate(() => fixture.runs[0].id);
  const go = hash => page.evaluate(hash => { location.hash = hash; }, hash);
  await go(`#evaluate/tests/${runId}`);
  await page.locator(".test-run-detail").waitFor();
  assert.equal(await page.evaluate(() => fixture.performanceReads.length), 0);
  await page.getByRole("tab", { name: "통계 요약", exact: true }).click();
  const performance = page.locator(".test-performance-summary");
  await performance.locator("table").first().waitFor();
  const initialReads = await page.evaluate(() => fixture.performanceReads.length);
  await page.getByRole("navigation", { name: "통계 항목 페이지", exact: true }).getByRole("button", { name: "다음 페이지", exact: true }).click();
  await page.waitForFunction(() => fixture.statisticsReads.at(-1).offset === 10);
  await page.locator(".test-stat-table tbody tr").first().waitFor();
  assert.equal(await page.evaluate(() => fixture.performanceReads.length), initialReads, "Paging does not rerun performance aggregation");
  await page.getByRole("combobox", { name: "집계 기준", exact: true }).selectOption("company_name");
  await page.waitForFunction(() => fixture.statisticsReads.at(-1).group_by === 'company_name');
  assert.equal(await page.evaluate(() => fixture.performanceReads.length), initialReads, "Grouping does not rerun performance aggregation");
  await page.getByRole("button", { name: "과탐 방향", exact: true }).click();
  await page.waitForFunction(() => fixture.performanceReads.at(-1).query.case_filter);
  await performance.locator("table").first().waitFor();
  await page.getByRole("group", { name: "빠른 필터", exact: true }).getByRole("button", { name: "전체", exact: true }).click();
  await page.waitForFunction(() => !fixture.performanceReads.at(-1).query.case_filter);
  for (const theme of ["SK", "Light", "Dark"]) {
    await page.getByRole("button", { name: /^(테마|Theme)$/ }).click();
    await page.getByRole("button", { name: theme, exact: true }).click();
    for (const locale of ["KR", "EN"]) {
      await page.getByRole("button", { name: locale, exact: true }).click();
      await performance.locator("table").first().waitFor();
      await performance.scrollIntoViewIfNeeded();
      await page.screenshot({ path: join(output, `${theme}-${locale}.png`), fullPage: true });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.doesNotMatch(await performance.innerText(), /SYNTHETIC_PRIVATE_ERROR|evidence_source_unavailable/);
      assert.ok((await performance.innerText()).includes(locale === "KR" ? "처리 시간과 출력" : "Processing time and output"));
    }
  }
  await page.getByRole("button", { name: "KR", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await performance.scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: join(output, "mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  const refresh = performance.getByRole("button", { name: /다시 조회|새로고침|Refresh/ });
  await page.evaluate(() => { fixture.performanceMode = 'error'; });
  await refresh.focus(); await page.keyboard.press("Enter");
  await performance.getByRole("alert").waitFor();
  assert.doesNotMatch(await performance.innerText(), /SYNTHETIC_PRIVATE_ERROR/);
  assert.equal(await performance.locator("table").count(), 0, "Failed refresh hides stale telemetry");
  await page.evaluate(() => { fixture.performanceMode = 'missing'; });
  await refresh.click(); await page.waitForFunction(() => fixture.performanceMode === 'missing');
  await performance.locator("table").first().waitFor();
  assert.ok((await performance.innerText()).includes("미기록"));
  await page.screenshot({ path: join(output, "missing.png"), fullPage: true });
  await page.evaluate(() => { fixture.performanceMode = 'normal'; fixture.performanceHold = true; });
  await refresh.click();
  await page.waitForFunction(() => typeof fixture.releasePerformance === 'function');
  await go(`#evaluate/tests/${runId}`);
  await page.waitForFunction(() => !document.querySelector('.test-performance-summary'));
  await page.evaluate(() => fixture.releasePerformance());
  await page.getByRole("tab", { name: "통계 요약", exact: true }).click();
  await performance.locator("table").first().waitFor();
  assert.deepEqual(errors, []); assert.deepEqual(network, []);
  console.log(JSON.stringify({ result: "passed", screenshots: 8, network: network.length, pageErrors: errors.length, checks: ["KR/EN", "SK/Light/Dark", "mobile overflow", "scope and pagination", "keyboard refresh", "missing and failure", "unmount stale response"] }));
} finally { await browser.close(); }
