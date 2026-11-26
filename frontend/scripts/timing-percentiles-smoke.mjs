// Real React views with synthetic timing values only. Every network destination
// is blocked; this script cannot call a model or access an existing database.
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { r5BrowserFixture } from "./r5-browser-fixture.mjs";

const output = fileURLToPath(new URL("../../test-results/timing-percentiles", import.meta.url));
mkdirSync(output, { recursive: true });
const run = "00000008-1111-4111-8111-111111111111";
const mock = r5BrowserFixture().replace("export const api=", `
state.performanceReads=[];state.analysisPerformanceReads=[];state.holdTest=false;state.holdAnalysis=false;state.failTest=false;state.failAnalysis=false;
const timing=n=>({recorded:2,unrecorded:1,mean_ms:n,p50_ms:n*.8,p95_ms:n*1.4,p99_ms:n*1.8,min_ms:n/2,max_ms:n*2});
const times={decision_elapsed_ms:timing(24000),analysis_queue_ms:timing(4000),analysis_processing_ms:timing(20000),result_ready_elapsed_ms:timing(27000)};
const timingBase={cases:3,completed_cases:3,statuses:{completed:3,pending:0,processing:0,failed:0,canceled:0},completed_timings:times};
const stat={cases:3,completed:3,failed:0,pending:0,processing:0,canceled:0,true_positive:1,false_positive:1,inconclusive:1,distribution_total:3,verdict_rates:{true_positive:1/3,false_positive:1/3,inconclusive:1/3},comparable:3,labeled:3,matches:2,accuracy:2/3,false_negatives:0,false_positives:0};
methods.testStatistics=async which=>({test_run_id:which,summary:stat,items:[{...stat,value:'검토용 시그니처'}],total_groups:1,reference_basis:'ground_truth',rejected:0,duplicates:0});
methods.testPerformance=async(which,query)=>{state.performanceReads.push(query);if(state.holdTest){state.holdTest=false;await new Promise(resolve=>state.releaseTest=resolve);}if(state.failTest)throw Object.assign(Error('SYNTHETIC_PRIVATE_ERROR'),{status:503});return {version:'test-performance-summary-v1',test_run_id:which,...timingBase,timings:times,phases:{},roles:{},display:{states:{completed:3},no_job_cases:0}};};
methods.analysisPerformance=async filters=>{state.analysisPerformanceReads.push(filters);if(state.holdAnalysis){state.holdAnalysis=false;await new Promise(resolve=>state.releaseAnalysis=resolve);}if(state.failAnalysis)throw Object.assign(Error('SYNTHETIC_PRIVATE_ERROR'),{status:503});return {version:'analysis-performance-summary-v1',...timingBase};};
const runtimeWithTimings=methods.runtimeStatus;
methods.runtimeStatus=async(...args)=>({...await runtimeWithTimings(...args),completed_cases:3,completed_timings:times});
export const api=`);
const bundle = await build({ entryPoints: [fileURLToPath(new URL("../src/main.jsx", import.meta.url))], plugins: [{ name: "offline-timings", setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }], bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  page.setDefaultTimeout(15000); const errors = [], network = []; let screenshots = 0;
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => route.request().url().split("#")[0] === "https://fixture.invalid/" ? route.fulfill({ contentType: "text/html", body: '<html lang="ko"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' }) : (network.push(route.request().url()), route.abort()));
  await page.goto("https://fixture.invalid/");
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  await page.locator(".r3-overview").waitFor();
  const go = hash => page.evaluate(hash => { location.hash = hash; scrollTo(0, 0); }, hash);
  const theme = async name => { await page.getByRole("button", { name: /^(테마|Theme)$/ }).click(); await page.getByRole("button", { name, exact: true }).click(); };
  for (const [color, locale, width] of [["Light", "KR", 1440], ["Dark", "EN", 1440], ["SK", "KR", 390]]) {
    await page.setViewportSize({ width, height: 1000 }); await theme(color); await page.getByRole("button", { name: locale, exact: true }).click();
    for (const [name, hash, selector] of [["test", `#evaluate/tests/${run}/statistics`, ".test-performance-summary"], ["production", "#operate/inference", ".production-performance-summary"], ["runtime", "#operate/runtime", ".completed-timing-summary"]]) {
      await go(hash); const scope = page.locator(selector); await scope.waitFor();
      if (name === "production") await scope.locator("details > summary").click();
      await scope.locator('.timing-percentiles strong').filter({ hasText: locale === "KR" ? "36.0초" : "36.0 s" }).waitFor();
      assert.equal(await scope.locator(".timing-percentiles").count(), 4);
      for (const percentile of ["P50", "P95", "P99"]) assert.equal(await scope.locator(".timing-percentiles > span > span").filter({ hasText: new RegExp(`^${percentile}$`) }).count(), 4);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, name + " page overflow");
      await page.screenshot({ path: join(output, `${name}-${color}-${locale}-${width}.png`), fullPage: true }); screenshots++;
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await go(`#evaluate/tests/${run}/statistics`);
  let scope = page.locator(".test-performance-summary"); await scope.locator(".timing-percentiles").first().waitFor();
  await scope.locator(".oe-performance-details > summary").click();
  await scope.locator(".timing-summary-cards").evaluate(node => { window.retainedTimingCards = node; });
  await page.evaluate(() => { fixture.holdTest = true; });
  await scope.getByRole("button", { name: "다시 조회", exact: true }).click();
  await page.waitForFunction(() => typeof fixture.releaseTest === "function");
  assert.equal(await page.evaluate(() => document.querySelector('.test-performance-summary .timing-summary-cards') === window.retainedTimingCards), true, "Test cards remain mounted while refreshing");
  assert.equal(await scope.locator(".oe-performance-details").getAttribute("open"), "");
  await page.evaluate(() => fixture.releaseTest()); await page.waitForFunction(() => !document.querySelector('.test-performance-summary').getAttribute('aria-busy')?.includes('true'));
  await page.evaluate(() => { fixture.failTest = true; });
  await scope.getByRole("button", { name: "다시 조회", exact: true }).click(); await scope.getByRole("alert").waitFor();
  assert.equal(await page.evaluate(() => document.querySelector('.test-performance-summary .timing-summary-cards') === window.retainedTimingCards), true, "Temporary error retains clearly marked previous Test data");
  assert.doesNotMatch(await scope.innerText(), /SYNTHETIC_PRIVATE_ERROR/);
  await go("#operate/inference"); scope = page.locator(".production-performance-summary"); await scope.locator("details > summary").click(); await scope.locator(".timing-summary-cards").waitFor();
  await scope.locator(".timing-summary-cards").evaluate(node => { window.retainedTimingCards = node; });
  await page.evaluate(() => { fixture.holdAnalysis = true; });
  await scope.getByRole("button", { name: "다시 조회", exact: true }).click(); await page.waitForFunction(() => typeof fixture.releaseAnalysis === "function");
  assert.equal(await page.evaluate(() => document.querySelector('.production-performance-summary .timing-summary-cards') === window.retainedTimingCards), true, "Production cards remain mounted while refreshing");
  assert.equal(await scope.locator("details").getAttribute("open"), "");
  await page.evaluate(() => fixture.releaseAnalysis());
  assert.deepEqual(await page.evaluate(() => fixture.analysisPerformanceReads.flatMap(query => ['offset', 'limit', 'sort_by', 'sort_order'].filter(key => key in query))), []);
  assert.deepEqual(errors, []); assert.deepEqual(network, []); assert.deepEqual(await page.evaluate(() => fixture.unknown), []); assert.deepEqual(await page.evaluate(() => fixture.writes), []);
  console.log(JSON.stringify({ status: "passed", screenshots, output, checks: ["Test, Production and Runtime", "P50/P95/P99 and independent timing scopes", "SK/Light/Dark", "KR/EN", "1440px/390px", "same-scope cards and disclosures retained during refresh", "temporary errors marked without raw error text", "no network/models/writes"] }));
} catch (error) { await page?.screenshot({ path: join(output, "failure.png"), fullPage: true }); throw error; }
finally { await browser.close(); }
