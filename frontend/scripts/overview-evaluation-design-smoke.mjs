// Synthetic offline browser review of the overview and evaluation page family.
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { r5BrowserFixture } from "./r5-browser-fixture.mjs";

const output = fileURLToPath(new URL("../../test-results/overview-evaluation-design", import.meta.url));
mkdirSync(output, { recursive: true });
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const run = "00000008-1111-4111-8111-111111111111";
const fixture = r5BrowserFixture().replace("export const api=", `
runtime.request_volume_series=Array.from({length:12},(_,index)=>({from:new Date(Date.parse(date)-86400000+index*7200000).toISOString(),completed:index===9?2:index===10?1:0,failed:0,pending:0,processing:0,canceled:0}));
Object.assign(runtime.outcome_summary,{pending:0,processing:0,canceled:0});
const statRow={value:'Synthetic signature',values:{signature:'Synthetic signature'},cases:3,completed:3,failed:0,pending:0,processing:0,true_positive:1,false_positive:1,inconclusive:1,distribution_total:3,verdict_rates:{true_positive:1/3,false_positive:1/3,inconclusive:1/3},comparable:3,labeled:3,matches:2,accuracy:2/3,false_negatives:1,false_positives:0};
const duration=(mean=1200)=>({recorded:3,unrecorded:0,mean_ms:mean,p50_ms:mean,p95_ms:mean*2,max_ms:mean*2,min_ms:mean/2});
const timing={decision_elapsed_ms:duration(2300),analysis_queue_ms:duration(500),analysis_processing_ms:duration(1800),result_ready_elapsed_ms:duration(2600)};
Object.assign(methods,{analysisEvidenceMeta:()=>null,testStatistics:async id=>({test_run_id:id,items:[statRow],summary:statRow,total_groups:1,scope_cases:3,rejected:0,duplicates:0,reference_basis:'ground_truth'}),testPerformance:async id=>({version:'test-performance-summary-v1',test_run_id:id,cases:3,completed_cases:3,statuses:{completed:3},timings:timing,completed_timings:timing,phases:{},roles:{},display:{states:{completed:3},no_job_cases:0,queue_ms:duration(100),processing_ms:duration(200)}})});
const originalTestRun=methods.testRun;methods.testRun=async(id,query,...rest)=>{globalThis.__designRunQuery=query;return originalTestRun(id,query,...rest);};
export const api=`).replace("get(target,name){", "get(target,name){if(name==='analysisEvidenceMeta')return methods.analysisEvidenceMeta;");
const cssImports = [...readFileSync(new URL("../src/main.jsx", import.meta.url), "utf8").matchAll(/import\s+["'](.+\.css)["'];/g)].map(match => `import ${JSON.stringify(match[1])};`).join("");
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';${cssImports}createRoot(document.getElementById('root')).render(<App/>);`, loader: "jsx", resolveDir: fileURLToPath(new URL("../src", import.meta.url)) }, plugins: [{ name: "offline-api", setup(builder) { builder.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: fixture, loader: "js" })); } }], bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const browser = await chromium.launch({ executablePath: join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
const screenshots = [], errors = [], traffic = [];
try {
  const focused = process.env.DESIGN_SMOKE_FOCUS === "1";
  for (const theme of process.env.DESIGN_THEME ? [process.env.DESIGN_THEME] : focused ? ["light"] : ["sk", "light", "dark"]) for (const locale of process.env.DESIGN_LOCALE ? [process.env.DESIGN_LOCALE] : focused ? ["ko"] : ["ko", "en"]) for (const width of process.env.DESIGN_WIDTH ? [Number(process.env.DESIGN_WIDTH)] : [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 960 }, reducedMotion: "reduce" });
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/*", route => route.request().url().split("#")[0] === "https://fixture.invalid/" ? route.fulfill({ contentType: "text/html", body: '<html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div></body></html>' }) : (traffic.push(route.request().url()), route.abort()));
    await page.goto("https://fixture.invalid/#overview");
    await page.evaluate(({ theme, locale }) => { localStorage.setItem("waf-console-theme", theme); localStorage.setItem("waf-console-locale", locale); }, { theme, locale });
    await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
    await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
    const routes = [["overview", "#overview", ".oe-home-verdict"], ["tests", "#evaluate/tests", ".oe-test-history"], ["test-cases", `#evaluate/tests/${run}`, ".oe-test-inline-totals"], ["evaluation", `#evaluate/tests/${run}/metrics`, ".oe-evaluation-metrics"], ["statistics", `#evaluate/tests/${run}/statistics`, ".oe-stat-results-head"]];
    for (const [name, hash, selector] of routes) {
      await page.evaluate(hash => { location.hash = hash; scrollTo(0, 0); }, hash);
      await page.locator(selector).first().waitFor();
      await page.waitForFunction(selector => { const element = document.querySelector(selector), busy = element?.closest('[aria-busy]'); return element && (!busy || busy.getAttribute('aria-busy') !== 'true'); }, selector);
      if (name === 'overview') await page.waitForFunction(() => document.querySelector('.oe-home-verdict strong')?.textContent !== '—');
      await page.evaluate(() => document.fonts.ready);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const noOverflow = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
      if (!noOverflow) {
        await page.screenshot({ path: join(output, `overflow-${name}-${locale}-${theme}-${width}.png`), fullPage: true });
        console.log(JSON.stringify(await page.evaluate(() => [...document.querySelectorAll('main *')].filter(element => { const rect = element.getBoundingClientRect(); return rect.right > innerWidth + 1 && !element.closest('.data-table-wrap,.table-wrap,.console-tabulator-wrap'); }).map(element => ({ tag: element.tagName, class: element.className, text: element.innerText?.slice(0, 80), right: element.getBoundingClientRect().right, width: element.getBoundingClientRect().width }))), null, 2));
      }
      assert.equal(noOverflow, true, `${name}/${theme}/${locale}/${width}: page overflow`);
      if (name === "test-cases") {
        assert.equal(await page.locator(".oe-test-detail-heading h1").count(), 1);
        assert.equal(await page.locator(".oe-test-inline-totals > div").count(), 4);
        assert.equal(await page.locator(".oe-run-configuration").getAttribute("open"), null);
      }
      if (name === "overview") {
        assert.equal(await page.locator(".oe-metric").count(), 4);
        assert.equal(await page.locator(".oe-home-recent").count(), 1);
        assert.equal(await page.locator(".oe-home-official").getAttribute("open"), null);
      }
      if (name === "evaluation") {
        assert.equal(await page.locator("[data-matrix-cell]").count(), 9);
        assert.equal(await page.locator(".oe-evaluation-more").getAttribute("open"), null);
      }
      if (name === "statistics") {
        assert.equal(await page.locator(".test-stat-overview > div").count(), 4);
        assert.equal(await page.locator(".oe-performance-details").getAttribute("open"), null);
        assert.equal(await page.locator(".oe-statistics-more").getAttribute("open"), null);
      }
      const file = `${name}-${locale}-${theme}-${width}.png`;
      await page.screenshot({ path: join(output, file), fullPage: true }); screenshots.push(file);
      if (name === "evaluation" && width === 1440 && theme === "light" && locale === "ko") {
        const verdicts = ["true_positive", "false_positive", "inconclusive"];
        for (const [index, cell] of ["tp", "fn", "abstained_positive", "fp", "tn", "abstained_negative", "expected_hold_positive", "expected_hold_negative", "expected_hold_match"].entries()) {
          await page.locator(`[data-matrix-cell="${cell}"]`).click();
          await page.locator(".test-run-cases").waitFor();
          assert.match(await page.locator(".test-run-cases").innerText(), /선택한 셀:/);
          await page.waitForFunction(({ expected, actual }) => globalThis.__designRunQuery?.reference_verdict === expected && globalThis.__designRunQuery?.verdict === actual, { expected: verdicts[Math.floor(index / 3)], actual: verdicts[index % 3] });
          await page.evaluate(hash => { location.hash = hash; }, hash);
          await page.locator("[data-matrix-cell]").first().waitFor();
        }
      }
    }
    await page.close();
  }
  assert.deepEqual(errors, []); assert.deepEqual(traffic, []);
  writeFileSync(join(output, "audit.json"), JSON.stringify({ screenshots, errors, traffic }, null, 2));
  console.log(`${screenshots.length} overview/evaluation screenshots saved to ${output}`);
} finally { await browser.close(); }
