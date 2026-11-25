// Actual React, Tabulator and CSS with synthetic API responses only.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { r5BrowserFixture } from "./r5-browser-fixture.mjs";

const output = fileURLToPath(new URL("../../test-results/feedback-workspace", import.meta.url));
mkdirSync(output, { recursive: true });
const canary = '<img src=x onerror="window.feedbackInjected=true"><script>window.feedbackInjected=true</script>\n합성 의견이며 실행되면 안 됩니다.';
const mock = r5BrowserFixture().replace("export const api=", `
state.feedbackQueries=[];state.feedbackFailure=false;state.feedbackDetailFailure=false;state.holdFeedbackDetail=false;
methods.analysisEvidenceMeta=()=>null;
const feedbackCategories=['insufficient_evidence','evidence_mismatch','explanation_error','exaggerated_impact','inappropriate_severity','other'];
const feedbackToday=new Date(Date.now()+9*3600000).toISOString().slice(0,10);
state.feedbackVersionIds=[id(300),id(301)];state.feedbackToday=feedbackToday;
const feedbackRows=Array.from({length:47},(_,n)=>{
 const production=n<36,version=production?[id(300),id(301),null][n%3]:null;
 const stamp=new Date(feedbackToday+'T03:00:00Z');stamp.setUTCDate(stamp.getUTCDate()-Math.floor(n/3));
 return {id:id(1000+n),analysis_id:n===0?'11111111-1111-4111-8111-111111111111':id((production?500:700)+n%6),event_id:'FEEDBACK-SYNTHETIC-'+String(n+1).padStart(3,'0'),analysis_purpose:production?'production':'test',source_system:'synthetic-collector',external_review_id:'synthetic-review-'+n,
 company_name:n===1?'<b>합성 회사 1</b>':'합성 회사 '+n%3,signature:'합성 요청 탐지 '+String(n+1).padStart(2,'0'),verdict:'inconclusive',severity:'UNKNOWN',
 production_configuration_id:version,production_configuration_label:version===id(300)?'v2.33 / 2026-11-10 14:02:17 KST':version?'v2.32 / 2026-11-08 11:10:03 KST':production?'운영 버전 미기록':null,test_run_id:production?null:id(8),
 feedback_categories:n===0||n%5===0?['evidence_mismatch','explanation_error']:n%7===0?[]:[feedbackCategories[n%6]],analyst_id:n===2?null:'합성 분석가 '+n%4,
 comment:n===0?${JSON.stringify(canary)}:n%7===0?'유형 없는 합성 의견':'원문과 설명의 연결을 확인하는 합성 의견 '+n,created_at:stamp.toISOString()};
});state.feedbackRows=feedbackRows;
function feedbackFiltered(query,ignoreVersion=false){return feedbackRows.filter(row=>{
 const day=new Date(new Date(row.created_at).getTime()+9*3600000).toISOString().slice(0,10);
 if(query.date_from&&day<query.date_from||query.date_to&&day>query.date_to)return false;
 if(query.analysis_purpose&&query.analysis_purpose!=='all'&&row.analysis_purpose!==query.analysis_purpose)return false;
 if(!ignoreVersion&&query.production_configuration_id&&(row.analysis_purpose!=='production'||(query.production_configuration_id==='__unrecorded__'?row.production_configuration_id!==null:row.production_configuration_id!==query.production_configuration_id)))return false;
 if(query.category&&!row.feedback_categories.includes(query.category))return false;
 if(query.analysis_id&&row.analysis_id!==query.analysis_id)return false;
 return !query.q||[row.event_id,row.analysis_id,row.company_name,row.signature,row.analyst_id,row.source_system,row.external_review_id].filter(Boolean).join(' ').toLowerCase().includes(query.q.toLowerCase());
});}
function feedbackGuard(){if(state.feedbackFailure)throw Object.assign(Error('PRIVATE_SQL_COMMENT_SELECT * FROM analyst_reviews'),{status:503});}
methods.feedback=async query=>{state.feedbackQueries.push({kind:'list',query});feedbackGuard();const rows=feedbackFiltered(query);return {items:rows.slice((query.page-1)*query.page_size,query.page*query.page_size),total:rows.length,page:query.page,page_size:query.page_size};};
methods.feedbackSummary=async query=>{state.feedbackQueries.push({kind:'summary',query});feedbackGuard();const rows=feedbackFiltered(query);return {registration_count:rows.length,analysis_count:new Set(rows.map(row=>row.analysis_id)).size,multi_category_count:rows.filter(row=>row.feedback_categories.length>1).length,comment_only_count:rows.filter(row=>!row.feedback_categories.length&&row.comment).length,category_counts:feedbackCategories.map(category=>({category,count:rows.filter(row=>row.feedback_categories.includes(category)).length,percent:rows.length?rows.filter(row=>row.feedback_categories.includes(category)).length*100/rows.length:0})),search_scope:'metadata_only'};};
methods.feedbackVersions=async query=>{state.feedbackQueries.push({kind:'versions',query});feedbackGuard();const rows=feedbackFiltered(query,true).filter(row=>row.analysis_purpose==='production');return {items:[id(300),id(301),'__unrecorded__'].map(version=>{const selected=rows.filter(row=>(row.production_configuration_id||'__unrecorded__')===version);return {id:version,label:version===id(300)?'v2.33 / 2026-11-10 14:02:17 KST':version===id(301)?'v2.32 / 2026-11-08 11:10:03 KST':'운영 버전 미기록',count:selected.length,analysis_count:new Set(selected.map(row=>row.analysis_id)).size,configuration_hash:null,applied_at:null};}).filter(row=>row.count),unrecorded_count:rows.filter(row=>!row.production_configuration_id).length};};
methods.feedbackDetail=async value=>{state.feedbackQueries.push({kind:'detail',id:value});if(state.feedbackDetailFailure)throw Object.assign(Error('PRIVATE_DETAIL_SQL'),{status:503});const row=feedbackRows.find(row=>row.id===value);if(state.holdFeedbackDetail)return new Promise(resolve=>{state.releaseFeedbackDetail=()=>resolve(row);});return row;};
const feedbackOldAnalysis=methods.analysis;
methods.analysis=async value=>{const detail=await feedbackOldAnalysis(value),row=feedbackRows.find(row=>row.analysis_id===value);return {...detail,analysis_purpose:row?.analysis_purpose||detail.analysis_purpose,test_run_id:row?.test_run_id||null};};
export const api=`).replace("get(target,name){", "get(target,name){if(name==='analysisEvidenceMeta')return target[name];");
const bundle = await build({ stdin: { contents: `import {StrictMode} from 'react';import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';import './workspacePolish.css';createRoot(document.getElementById('root')).render(<StrictMode><App/></StrictMode>);`, loader: "jsx", resolveDir: fileURLToPath(new URL("../src", import.meta.url)) },
  plugins: [{ name: "offline-api", setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }],
  bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
console.log("Feedback smoke: bundle ready");
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
if (!driver) throw Error("An existing browser is required; no download is attempted.");
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
console.log("Feedback smoke: browser ready");
let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  page.setDefaultTimeout(10000);
  const errors = [], traffic = []; let screenshots = 0;
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => route.request().url().split("#")[0] === "https://fixture.invalid/" ? route.fulfill({ contentType: "text/html", body: '<html lang="ko"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' }) : (traffic.push(route.request().url()), route.abort()));
  async function mount(hash = "#operate/feedback") {
    await page.goto("about:blank");
    await page.goto("https://fixture.invalid/" + hash);
    await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
    await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
    await page.locator(".feedback-workspace .tabulator-row").first().waitFor();
    await page.locator(".feedback-table-target strong").first().waitFor();
  }
  const workspace = () => page.locator(".feedback-workspace");
  const rows = () => workspace().locator(".tabulator-row");
  const apply = () => workspace().getByRole("button", { name: /^(조회|Apply)$/, exact: true }).click();
  const lastQuery = () => page.evaluate(() => fixture.feedbackQueries.filter(read => read.kind === "list").at(-1)?.query);
  const ready = () => page.waitForFunction(() => !document.querySelector(".feedback-loading"));
  const reset = async () => { await workspace().getByRole("button", { name: /^(초기화|Reset)$/, exact: true }).click(); await ready(); };
  const snap = async name => {
    await page.evaluate(async () => { window.scrollTo({ top: 0, behavior: "instant" }); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, name + " has no page overflow");
    await page.screenshot({ path: join(output, name + ".png"), fullPage: true }); screenshots++;
  };
  await mount(); await ready();
  console.log("Feedback smoke: workspace mounted");
  assert.equal(await rows().count(), 20, "only the server page is rendered");
  assert.equal((await lastQuery()).analysis_purpose, "production");
  assert.deepEqual(await workspace().locator(".feedback-metric strong").allTextContents(), ["36건", "7건", "8건"]);
  assert.equal(await workspace().locator(".feedback-category-bar").count(), 6);
  assert.match(await rows().first().innerText(), /<img src=x/);
  assert.equal(await workspace().locator("img,script").count(), 0);
  assert.equal(await page.evaluate(() => Boolean(window.feedbackInjected)), false);
  assert.equal(await page.evaluate(() => [...document.querySelectorAll(".tabulator-row .tabulator-cell")].every(cell => cell.scrollHeight <= cell.clientHeight + 2)), true, "portal content fits row heights");
  await rows().first().focus(); await page.keyboard.press("ArrowDown");
  assert.equal(await rows().nth(1).evaluate(row => row === document.activeElement), true);
  await page.keyboard.press("Enter");
  let dialog = page.getByRole("dialog", { name: "피드백 상세", exact: true });
  await dialog.locator(".feedback-detail-comment").waitFor(); await snap("detail-keyboard");
  await page.keyboard.press("Escape"); await dialog.waitFor({ state: "hidden" });
  assert.equal(await rows().nth(1).evaluate(row => row === document.activeElement), true, "dialog restores keyboard focus");
  await rows().first().getByRole("button", { name: "피드백 상세 보기", exact: true }).click();
  await dialog.locator(".feedback-detail-comment").waitFor();
  assert.equal(await dialog.locator(".feedback-detail-comment").textContent(), canary);
  assert.equal(await dialog.locator("img,script").count(), 0);
  await page.keyboard.press("Escape");
  const dates = workspace().locator('input[type="date"]');
  const beforeInvalid = await page.evaluate(() => fixture.feedbackQueries.length);
  await dates.nth(0).fill("2026-11-26"); await dates.nth(1).fill("2026-11-25"); await apply();
  await workspace().getByRole("alert").filter({ hasText: "시작일은 종료일보다" }).waitFor();
  assert.equal(await page.evaluate(() => fixture.feedbackQueries.length), beforeInvalid);
  await reset();
  console.log("Feedback smoke: keyboard and dates passed");
  await workspace().getByRole("button", { name: "7일", exact: true }).click(); await ready();
  const rangeQuery = await lastQuery(), today = await page.evaluate(() => fixture.feedbackToday);
  assert.equal(rangeQuery.date_to, today); assert.equal((Date.parse(rangeQuery.date_to) - Date.parse(rangeQuery.date_from)) / 86400000, 6);
  await reset();
  const selects = workspace().locator(".feedback-filters select");
  await selects.nth(0).selectOption("test"); assert.equal(await selects.nth(1).isDisabled(), true); await apply(); await ready();
  assert.equal((await lastQuery()).analysis_purpose, "test"); assert.equal((await lastQuery()).production_configuration_id, undefined);
  assert.equal(await rows().count(), 11); await workspace().getByText("Test에는 운영 버전을 적용하지 않습니다.", { exact: true }).waitFor();
  await selects.nth(0).selectOption("all"); await apply(); await ready();
  assert.equal(await workspace().locator(".feedback-metric strong").first().textContent(), "47건");
  const versionId = await page.evaluate(() => fixture.feedbackVersionIds[0]);
  await selects.nth(1).selectOption(versionId); assert.equal(await selects.nth(0).inputValue(), "production"); await apply(); await ready();
  assert.equal((await lastQuery()).production_configuration_id, versionId); assert.equal(await rows().count(), 12);
  assert.equal(await workspace().locator(".feedback-version-item").count(), 3, "version facet ignores its own selection");
  await workspace().locator(".feedback-version-item").filter({ hasText: "버전 미기록" }).click(); await ready();
  assert.equal((await lastQuery()).production_configuration_id, "__unrecorded__");
  assert.match(await rows().first().innerText(), /버전 미기록/); await snap("unrecorded-production-version");
  await reset();
  await workspace().locator(".feedback-category-bar").filter({ hasText: "설명 오류" }).click(); await ready();
  assert.equal((await lastQuery()).category, "explanation_error");
  assert.equal(await workspace().locator(".feedback-category-bar.active").count(), 1);
  await reset();
  const search = workspace().locator('input[type="search"]');
  console.log("Feedback smoke: filters passed");
  await search.fill("합성"); await apply(); await ready();
  await workspace().getByRole("button", { name: "다음 페이지", exact: true }).click(); await ready();
  assert.equal((await lastQuery()).page, 2); assert.equal((await lastQuery()).page_size, 20); assert.equal(await rows().count(), 16);
  const returnHash = await page.evaluate(() => location.hash);
  assert.doesNotMatch(decodeURIComponent(returnHash), /합성/);
  assert.doesNotMatch(await page.evaluate(() => JSON.stringify(history.state)), /합성/);
  await rows().first().getByRole("button", { name: "피드백 상세 보기", exact: true }).click();
  await dialog.locator(".feedback-detail-comment").waitFor();
  const selectedHash = await page.evaluate(() => location.hash); assert.match(selectedHash, /selected=/);
  await dialog.getByRole("button", { name: "원 분석 보기", exact: true }).click();
  await page.locator(".inference-detail").waitFor();
  await page.getByRole("button", { name: /← (분석 )?피드백/, exact: true }).click(); await rows().first().waitFor(); await ready();
  assert.equal((await lastQuery()).page, 2); assert.equal(await search.inputValue(), "합성");
  assert.equal(await page.evaluate(() => location.hash), returnHash);
  await mount(selectedHash); await ready(); await dialog.locator(".feedback-detail-comment").waitFor();
  assert.equal(await search.inputValue(), "", "search metadata is not restored from URLs");
  await page.keyboard.press("Escape"); await reset();
  // Delayed responses for an old selection cannot reopen a different scope.
  await page.evaluate(() => { fixture.holdFeedbackDetail = true; });
  await rows().first().getByRole("button", { name: "피드백 상세 보기", exact: true }).click();
  await dialog.getByText("피드백을 불러오는 중입니다…", { exact: true }).waitFor();
  await page.evaluate(() => { location.hash = "#operate/feedback?category=other"; });
  await dialog.waitFor({ state: "hidden" });
  await page.evaluate(() => { fixture.holdFeedbackDetail = false; fixture.releaseFeedbackDetail(); });
  await ready(); assert.equal(await page.getByRole("dialog").count(), 0);
  await reset();
  for (const theme of ["SK", "Light", "Dark"]) {
    console.log("Feedback smoke: capturing " + theme);
    await page.getByRole("button", { name: /^(테마|Theme)$/, exact: true }).click(); await page.getByRole("button", { name: theme, exact: true }).click();
    for (const locale of ["KR", "EN"]) {
      await page.getByRole("button", { name: locale, exact: true }).click();
      await workspace().getByRole("heading", { name: locale === "KR" ? "피드백 이력" : "Feedback history", exact: true }).waitFor();
      await page.setViewportSize({ width: 1440, height: 1000 }); await snap("feedback-" + locale + "-" + theme + "-1440");
      await page.setViewportSize({ width: 390, height: 844 }); await snap("feedback-" + locale + "-" + theme + "-390");
      assert.equal(await page.evaluate(() => Boolean(window.feedbackInjected)), false);
      await page.setViewportSize({ width: 1440, height: 1000 });
    }
  }
  await page.getByRole("button", { name: "KR", exact: true }).click();
  await search.fill("no-matching-metadata"); await apply(); await ready();
  await workspace().getByText("조회 조건에 맞는 피드백이 없습니다.", { exact: true }).waitFor();
  assert.deepEqual(await workspace().locator(".feedback-metric strong").allTextContents(), ["0건", "0건", "0건"]); await snap("empty-results");
  await reset();
  await page.evaluate(() => { fixture.feedbackFailure = true; });
  await workspace().getByRole("button", { name: "피드백 새로고침", exact: true }).click();
  await workspace().getByRole("alert").waitFor();
  assert.doesNotMatch(await workspace().innerText(), /PRIVATE_|SELECT|analyst_reviews/);
  assert.deepEqual(await workspace().locator(".feedback-metric strong").allTextContents(), ["—건", "—건", "—건"]); await snap("safe-read-errors");
  await page.evaluate(() => { fixture.feedbackFailure = false; }); await workspace().getByRole("button", { name: "다시 조회", exact: true }).click(); await ready();
  await page.evaluate(() => { fixture.feedbackDetailFailure = true; });
  await rows().first().getByRole("button", { name: "피드백 상세 보기", exact: true }).click();
  await dialog.getByRole("alert").waitFor(); assert.doesNotMatch(await dialog.innerText(), /PRIVATE_/); assert.equal(await dialog.getByRole("button", { name: "원 분석 보기", exact: true }).isDisabled(), true);
  await page.keyboard.press("Escape");
  assert.deepEqual(traffic, [], "no external or service traffic");
  assert.deepEqual(errors, [], "no browser exceptions");
  console.log(JSON.stringify({ screenshots, checks: "actual Tabulator portals and row heights, keyboard rows and focus restoration, safe comment text, KST periods, purpose/category/version facets, server pagination, original-analysis return, URL reload, delayed selection cancellation, empty/error distinctions, SK/Light/Dark KR/EN at 1440/390; synthetic data only, no external traffic" }));
} catch (error) {
  if (page) await page.screenshot({ path: join(output, "failure.png"), fullPage: true });
  throw error;
} finally { await browser.close(); }
