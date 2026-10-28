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
const counts=(index)=>({cases:10,completed:10,failed:0,pending:0,processing:0,canceled:0,true_positive:6,false_positive:3,inconclusive:1,distribution_total:10,comparable:8,matches:index===1?4:7,labeled:8,accuracy:index===1?.5:.875,label_coverage:.8,false_negatives:1,false_positives:index===1?2:0,abstained:0,expected_hold_mismatches:index===1?1:0,unclassified_completed:0,verdict_rates:{true_positive:.6,false_positive:.3,inconclusive:.1}});
const signatureRows=Array.from({length:27},(_,i)=>({value:i===0?'SQL Injection':i===1?'XSS':i===26?null:'시그니처 '+String(i+1).padStart(2,'0'),...counts(i)}));
const aggregate=rows=>{const summary={...counts(0)};Object.keys(summary).filter(key=>typeof summary[key]==='number').forEach(key=>summary[key]=rows.reduce((sum,row)=>sum+row[key],0));summary.accuracy=summary.comparable?summary.matches/summary.comparable:null;summary.label_coverage=summary.labeled/summary.cases;summary.verdict_rates=Object.fromEntries(['true_positive','false_positive','inconclusive'].map(key=>[key,summary.distribution_total?summary[key]/summary.distribution_total:null]));return summary;};
const statisticsCases=signatureRows.flatMap((row,i)=>Array.from({length:10},(_,j)=>{const verdict=j<6?'true_positive':j<9?'false_positive':'inconclusive';const outcome=j>=8?'unlabeled':j===7?'false_negative':i===1&&j>=4&&j<6?'false_positive':i===1&&j===6?'expected_abstention_mismatch':'match';return {signature:row.value,company_name:'회사 '+['A','B','C'][j%3],waf_action:j<6?'D':'A',status:'completed',verdict,reference_verdict:j>=8?null:outcome==='false_positive'?'false_positive':outcome==='false_negative'?'true_positive':outcome==='expected_abstention_mismatch'?'inconclusive':verdict,evaluation_outcome:outcome,...counts(0),cases:1,completed:1,distribution_total:1,true_positive:j<6?1:0,false_positive:j>=6&&j<9?1:0,inconclusive:j===9?1:0,comparable:j<8?1:0,matches:outcome==='match'?1:0,labeled:j<8?1:0,false_negatives:outcome==='false_negative'?1:0,false_positives:outcome==='false_positive'?1:0,expected_hold_mismatches:outcome==='expected_abstention_mismatch'?1:0,abstained:0};}));
const matchesFilter=(row,node)=>{if(!node)return true;if(node.op)return node.op==='and'?node.rules.every(rule=>matchesFilter(row,rule)):node.rules.some(rule=>matchesFilter(row,rule));const value=row[node.field]??null,expected=node.value;if(node.operator==='is_empty')return value==null||value==='';if(node.operator==='is_not_empty')return value!=null&&value!=='';if(value==null)return false;switch(node.operator){case 'eq':return value===expected;case 'ne':return value!==expected;case 'contains':return String(value).toLowerCase().includes(expected.toLowerCase());case 'not_contains':return !String(value).toLowerCase().includes(expected.toLowerCase());case 'starts_with':return String(value).toLowerCase().startsWith(expected.toLowerCase());case 'ends_with':return String(value).toLowerCase().endsWith(expected.toLowerCase());case 'gt':return value>expected;case 'gte':return value>=expected;case 'lt':return value<expected;case 'lte':return value<=expected;default:throw Error('invalid fixture operator');}};
const originalTestRun=methods.testRun;
state.executionStatsMissing=false;
const executionSummary=source=>{const counters={version:'test-execution-summary-v1',cases:source.length,parser:{success:0,partial:0,failed:0,unrecorded:0,warnings_recorded:0,warnings_unrecorded:0,warnings:[]},verifier:{policy_recorded:0,executed:0,not_executed:0,unrecorded:0,not_applicable:0,awaiting_execution:0,parser_triggered:0,parser_only:0,parser_with_other:0,reasons:[]}};const warningCounts=new Map(),reasons=new Map();for(const row of source){const index=statisticsCases.indexOf(row)%10;const status=['success','partial','failed','unrecorded'][index%4];counters.parser[status]++;if(index%5===4||status==='unrecorded')counters.parser.warnings_unrecorded++;else{counters.parser.warnings_recorded++;if(status==='partial'||status==='failed'){const code=status==='partial'?'request_protocol_not_recognized':'request_line_not_recognized';warningCounts.set(code,(warningCounts.get(code)||0)+1);}}if(index===9){counters.verifier.not_applicable++;continue;}if(index===8){counters.verifier.unrecorded++;continue;}counters.verifier.policy_recorded++;if(index===7){counters.verifier.not_executed++;continue;}if(index===6){counters.verifier.awaiting_execution++;continue;}counters.verifier.executed++;const codes=[['parser_incomplete'],['parser_incomplete','signature_not_exact'],['low_confidence'],['primary_inconclusive','parser_incomplete'],['false_positive_but_denied'],['other']][index];if(codes.includes('parser_incomplete')){counters.verifier.parser_triggered++;counters.verifier[codes.length===1?'parser_only':'parser_with_other']++;}for(const code of codes){const value=reasons.get(code)||{code,cases:0,only_cases:0,with_other_cases:0};value.cases++;value[codes.length===1?'only_cases':'with_other_cases']++;reasons.set(code,value);}}counters.parser.warnings=[...warningCounts].map(([code,cases])=>({code,cases}));counters.verifier.reasons=[...reasons.values()];return counters;};
methods.testRun=async(value,query)=>{state.caseQueries.push(query);const result=await originalTestRun(value,query);const criteria=query?.group_filters?JSON.parse(query.group_filters):query?.group_field?[{field:query.group_field,value:query.group_missing?null:query.group_value}]:[];
const rows=statisticsCases.map((row,index)=>({...row,index})).filter(row=>matchesFilter(row,query?.case_filter)&&criteria.every(({field,value})=>row[field]===value)&&['reference_verdict','verdict','evaluation_outcome'].every(field=>query?.[field]==null||row[field]===query[field]));const offset=Number(query?.offset||0),limit=Number(query?.limit||25);
state.exportCount=rows.length;return {...result,total_items:rows.length,items:rows.slice(offset,offset+limit).map(row=>({...result.items[row.index%result.items.length],id:id(600+row.index),analysis_id:id(1000+row.index),row_number:row.index+1,verdict:row.verdict,evaluation:{outcome:row.evaluation_outcome,reference_label:row.reference_verdict?{verdict:row.reference_verdict}:null},ground_truth_source:null}))};};
methods.testStatistics=async(value,query)=>{state.statisticsReads.push(query);const fields=query.group_by.split(',');const source=statisticsCases.filter(row=>matchesFilter(row,query.case_filter));const grouped=new Map();for(const row of source){const key=JSON.stringify(fields.map(field=>row[field]??null));if(!grouped.has(key))grouped.set(key,[]);grouped.get(key).push(row);}let rows=[...grouped.values()].map(group=>({value:group[0][fields[0]]??null,values:Object.fromEntries(fields.map(field=>[field,group[0][field]??null])),...aggregate(group)}));if(query.q)rows=rows.filter(row=>Object.values(row.values).some(value=>value?.includes(query.q)));return {test_run_id:value,group_by:query.group_by,group_fields:fields,items:rows.slice(Number(query.offset),Number(query.offset)+Number(query.limit)),total_groups:rows.length,limit:Number(query.limit),offset:Number(query.offset),summary:source.length?aggregate(source):{...aggregate([]),label_coverage:null},scope_cases:270,case_filter_applied:!!query.case_filter,rejected:0,duplicates:0,reference_basis:query.evaluation_id?'saved':query.reference_basis||'latest',accuracy_includes_inconclusive:true,...(state.executionStatsMissing?{}:{execution_summary:executionSummary(source)})};};
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
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.href.split("#")[0] === "https://fixture.invalid/") return route.fulfill({ contentType: "text/html", body: '<html lang="ko"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' });
    if (url.origin === "https://fixture.invalid" && /\/test-runs\/[^/]+\/export\/preview$/.test(url.pathname)) {
      assert.equal(route.request().method(), "POST");
      const count = await page.evaluate(() => fixture.exportCount);
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ count, scope: "a".repeat(64), columns: [
        { name: "event_id", label_ko: "이벤트 ID", description: "식별자", example: "sample", group: "input" },
        { name: "payload", label_ko: "HTTP 원문", description: "HTTP 원문", example: "GET /sample HTTP/1.1", group: "input" },
        { name: "verdict", label_ko: "AI 최종 판정", description: "판정", example: "true_positive", group: "result" }] }) });
    }
    if (url.origin === "https://fixture.invalid" && /\/test-runs\/[^/]+\/export.xlsx$/.test(url.pathname)) {
      assert.equal(route.request().method(), "POST");
      exports.push(route.request().postDataJSON());
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
      const execution = page.locator(".test-execution-summary");
      assert.deepEqual(await execution.locator(".test-execution-metrics dd").allTextContents(), ["162", "81", "27"]);
      assert.equal(await execution.locator(".test-execution-reason-table tbody tr").count(), 6);
      assert.equal(await execution.locator(".test-execution-warning-table tbody tr").count(), 2);
      assert.doesNotMatch(await execution.innerText(), /parser_incomplete|request_line_not_recognized|undefined|NaN/);
      assert.deepEqual(await execution.locator("th").evaluateAll(nodes => nodes.filter(node => node.scrollWidth > node.clientWidth + 2).map(node => node.textContent)), []);
      await execution.screenshot({ path: join(output, `execution-${locale}-${theme}.png`) }); screenshots++;
      await snap(`statistics-${locale}-${theme}`);
    }
  }
  await page.getByRole("button", { name: "KR", exact: true }).click();
  const pagination = page.getByRole("navigation", { name: "통계 항목 페이지", exact: true });
  await pagination.getByRole("button", { name: "마지막 페이지", exact: true }).click();
  await page.waitForFunction(() => fixture.statisticsReads.at(-1).offset === 20);
  await page.locator(".test-stat-table tbody tr").nth(1).waitFor();
  assert.equal(await page.locator(".test-stat-table tbody tr").count(), 7);
  assert.deepEqual(await page.locator(".test-execution-metrics dd").allTextContents(), ["162", "81", "27"], "group pagination must not narrow the execution scope");
  await pagination.getByRole("button", { name: "첫 페이지", exact: true }).click();
  await page.waitForFunction(() => fixture.statisticsReads.at(-1).offset === 0);
  await page.getByRole("combobox", { name: "집계 기준", exact: true }).selectOption("waf_action");
  await page.waitForFunction(() => fixture.statisticsReads.at(-1).group_by === "waf_action");
  await page.getByRole("button", { name: "차단", exact: true }).waitFor();
  await snap("waf-action");
  await page.getByRole("combobox", { name: "집계 기준", exact: true }).selectOption("company_name");
  const add = page.getByRole("button", { name: "기준 추가", exact: true });
  await add.focus(); await page.keyboard.press("Enter");
  await page.waitForFunction(() => fixture.statisticsReads.at(-1).group_by === "company_name,signature");
  assert.equal(await page.getByRole("combobox", { name: "집계 기준 2", exact: true }).inputValue(), "signature");
  assert.equal(await page.getByRole("combobox", { name: "집계 기준 2", exact: true }).locator('option[value="company_name"]').evaluate(option => option.disabled), true);
  await snap("company-signature");
  await add.click();
  await page.waitForFunction(() => fixture.statisticsReads.at(-1).group_by === "company_name,signature,waf_action");
  assert.equal(await add.isDisabled(), true);
  assert.equal(await page.locator(".test-stat-table th").count(), 9);
  for (const theme of ["SK", "Light", "Dark"]) {
    await page.getByRole("button", { name: /^(테마|Theme)$/ }).click(); await page.getByRole("button", { name: theme, exact: true }).click();
    for (const locale of ["KR", "EN"]) {
      await page.getByRole("button", { name: locale, exact: true }).click();
      assert.equal(await page.locator(".test-statistics > .data-table-wrap").evaluate(element => element.scrollWidth <= element.clientWidth + 2), true, "all three grouping fields and metrics fit on a desktop");
      await snap(`three-fields-${locale}-${theme}`);
    }
  }
  await page.getByRole("button", { name: "KR", exact: true }).click();
  await page.getByRole("textbox", { name: "항목 검색", exact: true }).fill("회사 B");
  await page.locator(".test-stat-controls").getByRole("button", { name: "검색", exact: true }).click();
  await page.waitForFunction(() => fixture.statisticsReads.at(-1).q === "회사 B");
  const combinedRow = page.locator(".test-stat-table tbody tr").first();
  assert.match(await combinedRow.innerText(), /회사 B/);
  await combinedRow.getByRole("button").first().click();
  await page.waitForFunction(() => !!fixture.caseQueries.at(-1).group_filters);
  const criteria = await page.evaluate(() => JSON.parse(fixture.caseQueries.at(-1).group_filters));
  assert.deepEqual(criteria, [{ field: "company_name", value: "회사 B" }, { field: "signature", value: "SQL Injection" }, { field: "waf_action", value: "D" }]);
  assert.equal(await page.locator(".test-run-cases .v5-context").innerText(), "선택한 항목: 회사: 회사 B · 시그니처: SQL Injection · WAF 조치: 차단");
  await snap("three-field-cases");
  await page.getByRole("button", { name: "문항 필터 초기화", exact: true }).click();
  await page.waitForFunction(() => !fixture.caseQueries.at(-1).group_filters && !fixture.caseQueries.at(-1).group_field);
  await page.getByRole("tab", { name: "통계 요약", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 }); await snap("three-fields-mobile");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "집계 기준 3 제거", exact: true }).click();
  await page.waitForFunction(() => fixture.statisticsReads.at(-1).group_by === "company_name,signature");
  assert.equal(await page.getByRole("textbox", { name: "항목 검색", exact: true }).inputValue(), "");
  await page.getByRole("button", { name: "집계 기준 2 제거", exact: true }).click();
  await page.waitForFunction(() => fixture.statisticsReads.at(-1).group_by === "company_name");
  await page.getByRole("combobox", { name: "집계 기준", exact: true }).selectOption("signature");
  await page.getByRole("button", { name: "SQL Injection", exact: true }).click();
  await page.waitForFunction(() => fixture.caseQueries.at(-1).group_field === "signature");
  assert.equal(await page.evaluate(() => fixture.caseQueries.at(-1).group_value), "SQL Injection");
  assert.equal(await page.getByRole("tab", { name: "문항별 결과", exact: true }).getAttribute("aria-selected"), "true");
  await snap("signature-cases");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Excel 다운로드", exact: true }).click();
  await page.getByRole("dialog", { name: "Excel 다운로드", exact: true }).getByRole("button", { name: "다운로드", exact: true }).click();
  assert.equal((await download).suggestedFilename(), `WAF-Test-${runId}.xlsx`);
  assert.equal(exports[0].filters.reference_basis, "latest");
  assert.equal(exports[0].filters.group_field, "signature"); assert.equal(exports[0].expected_count, 10);
  assert.equal(exports[0].filters.limit, undefined); assert.equal(exports[0].filters.offset, undefined);
  await page.getByRole("tab", { name: "통계 요약", exact: true }).click(); await page.locator(".test-stat-overview").waitFor();
  const prefilter = page.getByRole("region", { name: "집계 전 필터", exact: true });
  await prefilter.getByRole("button", { name: "과탐 방향", exact: true }).click();
  await page.waitForFunction(() => document.querySelector(".test-stat-overview strong")?.textContent === "2");
  assert.deepEqual(await page.locator(".test-execution-metrics dd").allTextContents(), ["2", "0", "0"], "applied case filters also scope execution statistics");
  assert.equal(await page.locator(".test-stat-table tbody tr").count(), 1);
  assert.match(await page.locator(".test-stat-overview").innerText(), /필터 전 270/);
  await snap("false-alarm-filter");
  await prefilter.getByRole("button", { name: "미탐 방향", exact: true }).click();
  await page.waitForFunction(() => document.querySelector(".test-stat-overview strong")?.textContent === "27");
  const beforeEdit = await page.evaluate(() => fixture.statisticsReads.length);
  await prefilter.getByRole("button", { name: "조건 편집", exact: true }).focus(); await page.keyboard.press("Enter");
  await prefilter.getByRole("combobox", { name: "조건 1.1 필드", exact: true }).selectOption("company_name");
  await prefilter.getByRole("combobox", { name: "조건 1.1 연산", exact: true }).selectOption("eq");
  await prefilter.getByRole("textbox", { name: "조건 1.1 값", exact: true }).fill("회사 A");
  await prefilter.getByRole("button", { name: "조건 추가", exact: true }).click();
  await prefilter.getByRole("combobox", { name: "조건 1.2 연산", exact: true }).selectOption("eq");
  await prefilter.getByRole("textbox", { name: "조건 1.2 값", exact: true }).fill("회사 B");
  await prefilter.getByRole("combobox", { name: "묶음 안 조건", exact: true }).selectOption("or");
  await prefilter.getByRole("button", { name: "묶음 추가", exact: true }).click();
  await prefilter.getByRole("combobox", { name: "조건 2.1 필드", exact: true }).selectOption("reference_verdict");
  await prefilter.getByRole("combobox", { name: "조건 2.1 값", exact: true }).selectOption("false_positive");
  await prefilter.locator("fieldset").nth(1).getByRole("button", { name: "조건 추가", exact: true }).click();
  await prefilter.getByRole("combobox", { name: "조건 2.2 필드", exact: true }).selectOption("verdict");
  await prefilter.getByRole("combobox", { name: "조건 2.2 값", exact: true }).selectOption("true_positive");
  assert.equal(await page.evaluate(() => fixture.statisticsReads.length), beforeEdit, "draft edits must not trigger aggregation");
  await prefilter.getByRole("button", { name: "필터 적용", exact: true }).click();
  await page.waitForFunction(() => document.querySelector(".test-stat-overview strong")?.textContent === "1");
  assert.equal(await page.evaluate(() => fixture.statisticsReads.at(-1).case_filter.rules[0].op), "or");
  assert.match(await prefilter.locator(".test-filter-summary").innerText(), /회사 같음 회사 A OR 회사 같음 회사 B/);
  await page.getByRole("combobox", { name: "집계 기준", exact: true }).selectOption("company_name");
  await page.getByRole("button", { name: "기준 추가", exact: true }).click();
  await page.getByRole("button", { name: "기준 추가", exact: true }).click();
  await page.waitForFunction(() => fixture.statisticsReads.at(-1).group_by === "company_name,signature,waf_action");
  for (const theme of ["SK", "Light", "Dark"]) {
    await page.getByRole("button", { name: /^(테마|Theme)$/ }).click(); await page.getByRole("button", { name: theme, exact: true }).click();
    for (const locale of ["KR", "EN"]) { await page.getByRole("button", { name: locale, exact: true }).click(); await snap(`filter-editor-${locale}-${theme}`); }
  }
  await page.getByRole("button", { name: "KR", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 }); await snap("filter-editor-mobile"); await page.setViewportSize({ width: 1440, height: 1000 });
  await page.locator(".test-stat-table tbody tr").first().getByRole("button").first().click();
  await page.waitForFunction(() => !!fixture.caseQueries.at(-1).case_filter);
  assert.equal(await page.evaluate(() => fixture.caseQueries.at(-1).case_filter.rules.length), 2);
  assert.match(await page.locator(".test-run-cases .test-filter-summary").innerText(), /집계 전 필터 적용/);
  await snap("prefiltered-cases");
  const filteredDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Excel 다운로드", exact: true }).click();
  await page.getByRole("dialog", { name: "Excel 다운로드", exact: true }).getByRole("button", { name: "다운로드", exact: true }).click(); await filteredDownload;
  assert.equal(exports[1].filters.case_filter.rules.length, 2); assert.equal(exports[1].expected_count, 1);
  assert.equal(exports[1].filters.limit, undefined); assert.equal(exports[1].filters.offset, undefined);
  await page.getByRole("tab", { name: "평가 상세", exact: true }).click();
  await page.getByRole("button", { name: /^공격을 오탐으로 판정:/ }).focus(); await page.keyboard.press("Enter");
  await page.waitForFunction(() => fixture.caseQueries.at(-1).evaluation_outcome === "false_negative");
  const matrixQuery = await page.evaluate(() => fixture.caseQueries.at(-1));
  assert.equal(matrixQuery.reference_verdict, "true_positive"); assert.equal(matrixQuery.verdict, "false_positive");
  assert.equal(matrixQuery.case_filter, undefined); assert.equal(matrixQuery.group_filters, undefined);
  assert.equal(await page.getByRole("tab", { name: "문항별 결과", exact: true }).getAttribute("aria-selected"), "true");
  const matrixRows = page.locator(".test-run-cases tbody tr");
  assert.equal(await matrixRows.count(), 25);
  for (const row of await matrixRows.all()) assert.match(await row.innerText(), /미탐 방향/);
  await snap("matrix-drilldown");
  await page.getByRole("navigation", { name: "테스트 문항 페이지", exact: true }).getByRole("button", { name: "마지막 페이지", exact: true }).click();
  await page.waitForFunction(() => fixture.caseQueries.at(-1).offset === 25);
  assert.equal(await matrixRows.count(), 2);
  await page.getByRole("button", { name: "문항 필터 초기화", exact: true }).click();
  await page.waitForFunction(() => !fixture.caseQueries.at(-1).case_filter && !fixture.caseQueries.at(-1).group_filters);
  await page.getByRole("tab", { name: "통계 요약", exact: true }).click();
  await page.waitForFunction(() => document.querySelector(".test-stat-overview strong")?.textContent === "1");
  await prefilter.getByRole("button", { name: "전체", exact: true }).click();
  await page.waitForFunction(() => document.querySelector(".test-stat-overview strong")?.textContent === "270");
  // Incomplete drafts show a safe local error without replacing the applied filter.
  const beforeInvalid = await page.evaluate(() => fixture.statisticsReads.length);
  await prefilter.getByRole("button", { name: "필터 적용", exact: true }).click(); await prefilter.getByRole("alert").waitFor();
  assert.equal(await page.evaluate(() => fixture.statisticsReads.length), beforeInvalid);
  await prefilter.getByRole("button", { name: "편집 닫기", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 }); await snap("statistics-mobile");
  const execution = page.locator(".test-execution-summary");
  await execution.screenshot({ path: join(output, "execution-mobile.png") }); screenshots++;
  const reasonRegion = page.getByRole("region", { name: "이유별 추가 검증 문항 수", exact: true });
  await reasonRegion.focus();
  assert.equal(await reasonRegion.evaluate(node => node === document.activeElement), true);
  await page.keyboard.press("ArrowRight");
  await page.waitForFunction(() => document.querySelector('.test-execution-summary [aria-label="이유별 추가 검증 문항 수"]').scrollLeft > 0);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.evaluate(() => { fixture.executionStatsMissing = true; });
  await page.locator(".test-statistics > .panel-head").getByRole("button", { name: "다시 조회", exact: true }).click();
  await page.getByText("이 실행에는 추가 검증 통계가 제공되지 않습니다.", { exact: true }).waitFor();
  assert.equal(await page.locator(".test-execution-metrics").count(), 0, "an older response must not fabricate zero counts");
  await snap("execution-unavailable");
  await page.evaluate(() => { fixture.executionStatsMissing = false; });
  await page.locator(".test-statistics > .panel-head").getByRole("button", { name: "다시 조회", exact: true }).click();
  await page.locator(".test-execution-metrics").waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "메뉴 열기", exact: true }).click();
  const menu = page.locator(".console-mobile-dialog");
  await menu.getByRole("button", { name: "연동", exact: true }).click();
  await menu.getByRole("button", { name: "API 키", exact: true }).click();
  await page.waitForFunction(() => location.hash === "#connect/api-keys");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await snap("integrations-sidebar");
  assert.deepEqual(network, []); assert.deepEqual(errors, []);
  assert.deepEqual(await page.evaluate(() => fixture.unknown), []);
  console.log(JSON.stringify({ screenshots, checks: "sidebar desktop/mobile, KR/EN, SK/Light/Dark, keyboard, Back/Forward, 1/2/3-field grouping, typed AND/OR prefilters, false alarm/miss directions, drafts/validation, filtered drilldown/clear, matrix selection clears stale filters and pages matching cases, pagination, filtered export modal without pagination", externalTraffic: 0 }));
} catch (error) {
  if (page) await page.screenshot({ path: join(output, "failure.png"), fullPage: true });
  throw error;
} finally { await browser.close(); }
