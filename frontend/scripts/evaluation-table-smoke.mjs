// Synthetic component test. All API calls are mocked in the bundle and every
// network request is blocked. No server, credentials, live DB or LLM is used.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdtempSync, readFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const source = fileURLToPath(new URL("../src", import.meta.url));
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
if (!driver) throw new Error("Existing Playwright required");
const { chromium } = await import(pathToFileURL(driver).href);
const output = mkdtempSync(join(tmpdir(), "waf-evaluation-table-"));
const mock = `
window.fixture = { answer: null, saves: 0, reads: [], versions: [], selected: [], waiting: false, opened: [] };
const state = window.fixture;
const wait = () => new Promise(resolve => setTimeout(resolve, 60));
const summary = answer => ({total:1,labeled:answer?1:0,evaluable:answer?1:0,matches:answer==='true_positive'?1:0,
  binary_decided:answer?1:0,binary_evaluable:answer?1:0,source_groups:[],outcomes:answer?{[answer==='true_positive'?'match':'false_positive']:1}:{unlabeled:1},
  metrics:{accuracy:answer?(answer==='true_positive'?1:0):null,precision:answer?(answer==='true_positive'?1:0):null,recall:answer==='true_positive'?1:null,f1:answer?(answer==='true_positive'?1:0):null}});
function run(query={}) {
  const answer = query.evaluation_id ? state.versions.find(v=>v.id===query.evaluation_id)?.answer : query.reference_basis==='latest' ? state.answer : null;
  return {id:'fixture-run',name:'운영 요청 검증',kind:'direct',status:state.waiting?'pending':'completed',execution_mode:'moduagent',created_at:'2026-09-15T00:00:00Z',
    total:1,accepted:1,rejected:0,pending:state.waiting?1:0,processing:0,completed:state.waiting?0:1,failed:0,accepting_items:state.waiting,
    total_elapsed_ms:1200,profile_metadata:{model_name:'가상 모델'},evaluation_summary:summary(answer),
    items:[{id:'fixture-item',analysis_id:'fixture-analysis',event_id:'hidden-event-id',case_name:'검색 입력 <img src="https://blocked.invalid/x">',
      row_number:1,ingest_status:'accepted',status:'completed',verdict:'true_positive',summary_ko:'가상 분석 결과입니다. '.repeat(35)+'마지막 문장',
      evaluation:{outcome:answer?(answer==='true_positive'?'match':'false_positive'):'unlabeled',reference_label:answer?{verdict:answer,revision:state.saves,source_kind:'reference',ai_visible:true}:null}}],
    total_items:1,facets:{difficulties:[],test_categories:[]}};
}
export const api = {
  testRun:async(id,query)=>{state.reads.push(query);await wait();return run(query);},
  testRuns:async(query)=>{state.reads.push(query);await wait();return {items:[run(query)],total:1};},
  testEvaluations:async()=>({items:state.versions}),
  referenceSelection:async(ids)=>{state.selected=ids;return {items:ids.map(analysis_id=>({analysis_id,expected_revision:state.saves,verdict:state.answer}))};},
  saveReferences:async(body)=>{state.answer=body.verdict;state.saves++;return {applied_count:body.targets.length};},
  rescoreTest:async()=>{const version={id:'saved-'+(state.versions.length+1),revision:state.versions.length+1,created_at:'2026-09-15T00:01:00Z',answer:state.answer};state.versions.push(version);return version;}
};`;
const bundle = await build({ stdin: { contents: `import {useState} from 'react';import {createRoot} from 'react-dom/client';
import {TestRunDetail,TestRunHistory} from './TestRuns.jsx';
function Fixture(){const [list,setList]=useState(false);return <><nav><button onClick={()=>setList(true)}>목록 보기</button><button onClick={()=>setList(false)}>상세 보기</button></nav>{list?<TestRunHistory onSelect={()=>setList(false)}/>:<TestRunDetail id="fixture-run" onBack={()=>setList(true)} onOpen={id=>window.fixture.opened.push(id)}/>}</>;}
createRoot(document.getElementById('root')).render(<Fixture/>);`, loader: "jsx", resolveDir: source },
  plugins: [{ name: "offline-api", setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }],
  bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' },
  loader: { ".css": "empty", ".md": "text" }, logLevel: "silent" });
const css = ["styles.css", "ux.css", "testRuns.css", "validationData.css", "evaluationMetrics.css", "referenceLabels.css", "dataTable.css"].map(file => readFileSync(join(source, file), "utf8")).join("\n");
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
const checks = [];
try {
  const page = await browser.newPage({ viewport: { width: 1360, height: 1000 } });
  const errors = []; let network = 0;
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => { network++; return route.abort(); });
  await page.setContent('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><main id="root" style="padding:24px;max-width:1280px;margin:auto"></main></body></html>');
  await page.addStyleTag({ content: css }); await page.addScriptTag({ content: bundle.outputFiles[0].text });
  const table = page.getByRole("table", { name: "문항별 분석 결과", exact: true });
  await table.waitFor(); assert.equal(await page.locator(".quality-metric-accuracy > strong").first().innerText(), "계산 불가");
  assert.equal(await page.locator("img").count(), 0); checks.push("inert input and unscored initial answer");
  async function save(answer) {
    await table.getByRole("checkbox", { name: "현재 페이지 전체 선택", exact: true }).check();
    await page.getByRole("button", { name: "참고 답안 일괄 입력", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "참고 답안 입력", exact: true });
    await dialog.getByLabel("참고 답안", { exact: true }).selectOption(answer);
    await dialog.getByRole("button", { name: "답안 저장", exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.quality-metric-accuracy > strong')?.textContent === (window.fixture.answer === 'true_positive' ? '100.0%' : '0.0%'));
  }
  await save("true_positive");
  assert.equal(await page.getByText("1건의 참고 답안을 저장했습니다.", { exact: true }).count(), 1);
  assert.match(await table.innerText(), /기준 정탐\s+일치/);
  assert.deepEqual(await page.evaluate(() => window.fixture.selected), ["fixture-analysis"]);
  checks.push("answer save immediately refreshes scores, rows, selection and persistent notice");
  await page.getByRole("button", { name: "평가 기록 저장", exact: true }).click();
  await page.getByRole("dialog", { name: "평가 기록 저장", exact: true }).getByRole("button", { name: "저장", exact: true }).click();
  await page.waitForFunction(() => window.fixture.reads.some(q => q.evaluation_id === 'saved-1') && document.querySelector('table.data-table'));
  await save("false_positive");
  assert.equal(await page.getByRole("combobox", { name: "참고 답안 기준", exact: true }).inputValue(), "latest");
  await page.getByRole("combobox", { name: "참고 답안 기준", exact: true }).selectOption("saved-1");
  await page.waitForFunction(() => document.querySelector('.quality-metric-accuracy > strong')?.textContent === '100.0%');
  await page.getByRole("combobox", { name: "참고 답안 기준", exact: true }).selectOption("initial");
  await page.waitForFunction(() => document.querySelector('.quality-metric-accuracy > strong')?.textContent === '계산 불가');
  await page.getByRole("combobox", { name: "참고 답안 기준", exact: true }).selectOption("latest");
  await page.waitForFunction(() => document.querySelector('.quality-metric-accuracy > strong')?.textContent === '0.0%');
  checks.push("initial and saved evaluations remain stable; saving from history switches to latest");
  await table.getByRole("checkbox", { name: "현재 페이지 전체 선택", exact: true }).check();
  await table.getByRole("button", { name: "문항 / 분류", exact: false }).click();
  await page.waitForFunction(() => window.fixture.reads.at(-1)?.sort_by === 'case_name' && document.querySelector('table.data-table'));
  assert.equal(await table.getByRole("checkbox", { name: "현재 페이지 전체 선택", exact: true }).isChecked(), false);
  assert.equal(await table.locator('th[aria-sort="ascending"]').count(), 1);
  await table.getByRole("button", { name: "문항 / 분류", exact: false }).press("Enter");
  await page.waitForFunction(() => window.fixture.reads.at(-1)?.sort_order === 'desc' && document.querySelector('table.data-table'));
  assert.equal(await page.evaluate(() => window.fixture.reads.at(-1).offset), 0);
  checks.push("keyboard/server sorting, page reset and selection scope");
  await table.locator("tbody .text-button").first().click();
  assert.deepEqual(await page.evaluate(() => window.fixture.opened), ["fixture-analysis"]);
  const summary = table.locator(".summary-preview-trigger"); await summary.focus();
  await page.getByRole("tooltip").waitFor(); assert.match(await page.getByRole("tooltip").innerText(), /마지막 문장/);
  await summary.press("Escape"); checks.push("immutable detail navigation and full summary on focus");
  await table.scrollIntoViewIfNeeded(); await page.screenshot({ path: join(output, "desktop.png"), fullPage: true, animations: "disabled" });
  await page.evaluate(() => document.documentElement.dataset.theme = "dark");
  await page.screenshot({ path: join(output, "dark.png"), fullPage: true, animations: "disabled" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: join(output, "mobile.png"), fullPage: true });
  const layout = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, viewport: window.innerWidth,
    overflow: [...document.querySelectorAll('body *')].filter(el => !el.closest('.table-wrap') && el.getBoundingClientRect().right > window.innerWidth + 1).map(el => ({tag:el.tagName,cls:el.className,width:el.getBoundingClientRect().width,right:el.getBoundingClientRect().right})).slice(0,20) }));
  assert.equal(layout.width <= layout.viewport + 1, true, JSON.stringify(layout));
  assert.equal(await table.evaluate(element => element.parentElement.scrollWidth > element.parentElement.clientWidth), true);
  await page.screenshot({ path: join(output, "mobile.png"), fullPage: true }); checks.push("light/dark themes and contained mobile horizontal scroll");
  await page.setViewportSize({ width: 1360, height: 1000 });
  await page.getByRole("button", { name: "목록 보기", exact: true }).click();
  const history = page.getByRole("table", { name: "테스트 목록", exact: true }); await history.waitFor();
  assert.match(await history.innerText(), /0.0%/); assert.equal(await page.evaluate(() => window.fixture.reads.at(-1).reference_basis), "latest");
  checks.push("test catalog uses latest reference metrics");
  await page.evaluate(() => window.fixture.waiting = true);
  await page.getByRole("button", { name: "상세 보기", exact: true }).click(); await table.waitFor();
  assert.equal(await page.getByRole("button", { name: "평가 기록 저장", exact: true }).isDisabled(), true);
  assert.match(await page.locator(".dataset-version-bar").innerText(), /분석이 완료되면/);
  checks.push("unfinished-run snapshot restriction has visible explanation");
  assert.deepEqual(errors, []); assert.equal(network, 0);
  console.log(JSON.stringify({ checks, network_requests: network, page_errors: errors, screenshots: output }, null, 2));
} finally { await browser.close(); }
