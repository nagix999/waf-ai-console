// Actual React/CSS, synthetic API fixtures and offline downloads only.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { r5BrowserFixture } from "./r5-browser-fixture.mjs";

const output = fileURLToPath(new URL("../../test-results/test-statistics", import.meta.url));
mkdirSync(output, { recursive: true });
const mock = r5BrowserFixture().replace("export const api=", `
state.statisticsReads=[];state.caseQueries=[];state.runs=runs;
Object.assign(runs[0],{name:'WAF 통계 예시',total:270,accepted:270,completed:270,status:'completed',failed:0,pending:0,processing:0,test_purpose:'development',evaluation_mode:'reference',ground_truth:null});
const counts=(index)=>({cases:10,completed:10,failed:0,pending:0,processing:0,canceled:0,true_positive:6,false_positive:3,inconclusive:1,distribution_total:10,comparable:8,matches:index===1?4:7,labeled:8,accuracy:index===1?.5:.875,label_coverage:.8,false_negatives:index===1?2:0,false_positives:1,abstained:1,expected_hold_mismatches:0,unclassified_completed:0,verdict_rates:{true_positive:.6,false_positive:.3,inconclusive:.1}});
const signatureRows=Array.from({length:27},(_,i)=>({value:i===0?'SQL Injection':i===1?'XSS':i===26?null:'시그니처 '+String(i+1).padStart(2,'0'),...counts(i)}));
const aggregate=rows=>{const summary={...counts(0)};Object.keys(summary).filter(key=>typeof summary[key]==='number').forEach(key=>summary[key]=rows.reduce((sum,row)=>sum+row[key],0));summary.accuracy=summary.matches/summary.comparable;summary.label_coverage=summary.labeled/summary.cases;return summary;};
const originalTestRun=methods.testRun;
methods.testRun=async(value,query)=>{state.caseQueries.push(query);const result=await originalTestRun(value,query);const total=query?.group_field?10:270;return {...result,total_items:total,items:Array.from({length:Math.min(query?.limit||25,total)},(_,i)=>({...result.items[i%result.items.length],id:id(600+i),analysis_id:id(700+i),row_number:i+1,ground_truth_source:null}))};};
methods.testStatistics=async(value,query)=>{state.statisticsReads.push(query);let rows=signatureRows;if(query.group_by==='waf_action')rows=[{value:'D',...aggregate(signatureRows.slice(0,18))},{value:'A',...aggregate(signatureRows.slice(18))}];if(query.q)rows=rows.filter(row=>row.value?.includes(query.q));return {test_run_id:value,group_by:query.group_by,items:rows.slice(Number(query.offset),Number(query.offset)+Number(query.limit)),total_groups:rows.length,limit:Number(query.limit),offset:Number(query.offset),summary:aggregate(signatureRows),rejected:0,duplicates:0,reference_basis:query.evaluation_id?'saved':query.reference_basis||'latest',accuracy_includes_inconclusive:true};};
export const api=`);
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: "jsx", resolveDir: fileURLToPath(new URL("../src", import.meta.url)) },
  plugins: [{ name: "offline-api", setup(builder) { builder.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }],
  bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce", acceptDownloads: true });
  const errors = [], network = [], exports = []; let screenshots = 0;
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => {
    const url = new URL(route.request().url());
    if (url.href.split("#")[0] === "https://fixture.invalid/") return route.fulfill({ contentType: "text/html", body: '<html lang="ko"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' });
    if (url.origin === "https://fixture.invalid" && /\/test-runs\/[^/]+\/export.xlsx$/.test(url.pathname)) {
      exports.push(Object.fromEntries(url.searchParams));
      return route.fulfill({ contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", body: "Offline workbook download fixture" });
    }
    network.push(url.href); return route.abort();
  });
  await page.goto("https://fixture.invalid/");
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  page.setDefaultTimeout(10000); await page.locator(".r3-overview").waitFor();
  const runId = await page.evaluate(() => fixture.runs[0].id);
  const go = hash => page.evaluate(hash => { location.hash = hash; }, hash);
  const snap = async name => {
    await page.evaluate(async () => { window.scrollTo({ top: 0, behavior: "instant" }); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, name);
    await page.screenshot({ path: join(output, name + ".png"), fullPage: true }); screenshots++;
  };
  await go(`#evaluate/tests/${runId}`); await page.locator(".test-run-detail").waitFor();
  assert.equal(await page.locator(".v5-workspace-tabs").count(), 0);
  assert.equal(await page.evaluate(() => fixture.statisticsReads.length), 0, "statistics fetches only when selected");
  const statsTab = page.getByRole("tab", { name: "통계 요약", exact: true });
  await statsTab.focus(); await page.keyboard.press("Enter"); await page.locator(".test-stat-overview").waitFor();
  assert.equal(await page.evaluate(() => location.hash), `#evaluate/tests/${runId}/statistics`);
  await page.goBack(); await page.getByRole("tab", { name: "문항별 결과", exact: true }).waitFor();
  assert.equal(await page.getByRole("tab", { name: "문항별 결과", exact: true }).getAttribute("aria-selected"), "true");
  await page.goForward(); await page.locator(".test-stat-overview").waitFor();
  for (const theme of ["SK", "Light", "Dark"]) {
    await page.getByRole("button", { name: /^(테마|Theme)$/ }).click(); await page.getByRole("button", { name: theme, exact: true }).click();
    for (const locale of ["KR", "EN"]) {
      await page.getByRole("button", { name: locale, exact: true }).click();
      await go(`#evaluate/tests/${runId}/statistics`); await page.locator(".test-stat-overview").waitFor();
      const nav = page.locator("aside .console-navigation");
      assert.equal(await nav.locator('[aria-current="page"]').innerText(), locale === "KR" ? "테스트" : "Tests");
      assert.equal(await page.locator(".test-stat-table tbody tr").count(), 10);
      assert.match(await page.locator(".test-stat-table tbody tr").first().innerText(), /87.5%/);
      await snap(`statistics-${locale}-${theme}`);
    }
  }
  await page.getByRole("button", { name: "KR", exact: true }).click();
  const pagination = page.getByRole("navigation", { name: "통계 항목 페이지", exact: true });
  await pagination.getByRole("button", { name: "마지막 페이지", exact: true }).click();
  await page.waitForFunction(() => fixture.statisticsReads.at(-1).offset === 20);
  await page.locator(".test-stat-table tbody tr").nth(1).waitFor();
  assert.equal(await page.locator(".test-stat-table tbody tr").count(), 7);
  await pagination.getByRole("button", { name: "첫 페이지", exact: true }).click();
  await page.waitForFunction(() => fixture.statisticsReads.at(-1).offset === 0);
  await page.getByRole("combobox", { name: "집계 기준", exact: true }).selectOption("waf_action");
  await page.waitForFunction(() => fixture.statisticsReads.at(-1).group_by === "waf_action");
  await page.getByRole("button", { name: "차단", exact: true }).waitFor();
  await snap("waf-action");
  await page.getByRole("combobox", { name: "집계 기준", exact: true }).selectOption("signature");
  await page.getByRole("button", { name: "SQL Injection", exact: true }).click();
  await page.waitForFunction(() => fixture.caseQueries.at(-1).group_field === "signature");
  assert.equal(await page.evaluate(() => fixture.caseQueries.at(-1).group_value), "SQL Injection");
  assert.equal(await page.getByRole("tab", { name: "문항별 결과", exact: true }).getAttribute("aria-selected"), "true");
  await snap("signature-cases");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "전체 문항 Excel", exact: true }).click();
  assert.equal((await download).suggestedFilename(), `WAF-Test-${runId}.xlsx`);
  assert.deepEqual(exports[0], { reference_basis: "latest" });
  await page.getByRole("tab", { name: "통계 요약", exact: true }).click(); await page.locator(".test-stat-overview").waitFor();
  await page.setViewportSize({ width: 390, height: 844 }); await snap("statistics-mobile");
  await page.getByRole("button", { name: "메뉴 열기", exact: true }).click();
  const menu = page.locator(".console-mobile-dialog");
  await menu.getByRole("button", { name: "연동", exact: true }).click();
  await menu.getByRole("button", { name: "API 키", exact: true }).click();
  await page.waitForFunction(() => location.hash === "#connect/api-keys");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await snap("integrations-sidebar");
  assert.deepEqual(network, []); assert.deepEqual(errors, []);
  assert.deepEqual(await page.evaluate(() => fixture.unknown), []);
  console.log(JSON.stringify({ screenshots, checks: "sidebar submenu desktop/mobile, single body tab layer, KR/EN, SK/Light/Dark, keyboard, Back/Forward, group pagination/drilldown, whole-Test download without row filters", externalTraffic: 0 }));
} catch (error) {
  if (page) await page.screenshot({ path: join(output, "failure.png"), fullPage: true });
  throw error;
} finally { await browser.close(); }
