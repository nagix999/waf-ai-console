// In-bundle fixtures and an existing development browser. No API/LLM/network.
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
window.fixture={writes:[],reads:[],created:[],catalogError:false,finalized:false};const state=window.fixture;
const agents={profiles:[{id:'p1',name:'기본 모델',provider:'vllm',can_assign:true},{id:'p2',name:'후보 모델',provider:'vllm',can_assign:true},{id:'blocked',name:'미검증 모델',provider:'vllm',can_assign:false}],assignments:{test:{primary_profile_id:'p1',verifier_profile_id:null,evidence_editor_enabled:false}}};
const versions=(prefix)=>({active_version_id:prefix+'1',items:[{id:prefix+'1',name:'운영 버전',version_number:1},{id:prefix+'2',name:'후보 버전',version_number:2}]});
const datasets=[{id:'empty',name:'미승인 fixture',revision:2,total:3,review_counts:{draft:3,approved:0}},{id:'approved',name:'승인 fixture',revision:8,total:3,review_counts:{draft:1,approved:2}}];
const submit=async(kind,payload)=>{state.writes.push({kind,payload});await new Promise(resolve=>setTimeout(resolve,200));return {id:'run-'+state.writes.length,accepted:1,rejected:0,duplicates:0};};
export const api={
 agentSettings:async()=>{if(state.catalogError)throw Error('private-error');return agents;},promptPolicies:async()=>versions('v'),inputSchemas:async()=>versions('s'),
 createTestRun:payload=>submit('direct',payload),uploadTestRun:(file,name,key,candidate)=>submit('file',{name,candidate_configuration:candidate}),
 searchValidationDatasets:async query=>{state.reads.push(query);return {items:datasets.filter(item=>item.name.includes(query.query)),total:2};},
 runValidationDataset:(id,payload)=>submit('dataset',{id,...payload}),
 testRun:async(id,query)=>({id,name:'공식 평가 fixture',kind:'dataset',status:'completed',created_at:'2026-09-17T00:00:00Z',evaluation_mode:'ground_truth',execution_mode:'moduagent',
 ground_truth:{dataset_name:'승인 fixture',dataset_revision:8,approved_count:2,excluded_count:1},official_evaluation_pending:!state.finalized&&!query.evaluation_id,total:2,accepted:2,rejected:0,pending:0,processing:0,completed:2,failed:0,total_elapsed_ms:3000,profile_metadata:{},
 evaluation_summary:{total:2,labeled:2,evaluable:2,matches:2,source_groups:[],outcomes:{match:2},confusion_matrix:{tp:1,tn:1},metrics:{accuracy:1}},items:[],total_items:0,facets:{difficulties:[],test_categories:[]}}),
 testEvaluations:async()=>({items:state.finalized?[{id:'saved',revision:1,evaluation_kind:'ground_truth',created_at:'2026-09-17T00:00:03Z'}]:[]}),
 testRetryEligibility:async()=>({failed_count:0,eligible_count:0,eligible_ids:[]})
};`;
const bundle = await build({ stdin: { contents: `import {useState} from 'react';import {createRoot} from 'react-dom/client';import {TestAnalysisPage} from './App.jsx';import {TestRunDetail} from './TestRuns.jsx';
function Fixture(){const [results,setResults]=useState(false);return <><button onClick={()=>setResults(!results)}>결과 화면 전환</button>{results?<TestRunDetail id="official" onBack={()=>setResults(false)} onOpen={()=>{}}/>:<TestAnalysisPage selectedRunId={null} onSelectRun={id=>window.fixture.created.push(id)} agentMode="moduagent" onViewTests={()=>{}} onOpen={()=>{}}/>}</>;}createRoot(document.getElementById('root')).render(<Fixture/>);`, loader: "jsx", resolveDir: source },
  plugins: [{ name: "offline-api", setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }], bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".css": "empty", ".md": "text" }, logLevel: "silent" });
const css = ["styles.css", "ux.css", "validationData.css", "dataTable.css", "candidateConfiguration.css", "testRuns.css"].map(file => readFileSync(join(source, file), "utf8")).join("\n");
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = []; let network = 0;
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => { network++; return route.abort(); });
  await page.setContent('<html lang="ko"><body><main id="root" style="padding:24px;max-width:1320px;margin:auto"></main></body></html>');
  await page.addStyleTag({ content: css }); await page.addScriptTag({ content: bundle.outputFiles[0].text });
  const configure = page.getByRole("button", { name: "구성 변경", exact: true }); await configure.click();
  const dialog = page.getByRole("dialog", { name: "테스트 실행 구성", exact: true });
  assert.equal(await dialog.getByRole("option", { name: /미검증 모델/ }).first().evaluate(option => option.disabled), true);
  await dialog.getByRole("combobox", { name: "1차 판정 · Primary", exact: true }).selectOption("p2");
  await dialog.getByRole("combobox", { name: "지침 버전", exact: true }).selectOption("v2");
  await dialog.getByRole("combobox", { name: "입력 스키마 버전", exact: true }).selectOption("s2");
  await dialog.getByRole("button", { name: "이번 테스트에 적용", exact: true }).click();
  const direct = page.getByRole("tabpanel", { name: "단건 분석", exact: true });
  for (const [label, value] of [["회사명","Fixture"],["WAF 벤더","fixture"],["출발지 IP","192.0.2.1"],["목적지 IP","198.51.100.1"],["HTTP 원문","GET /fixture HTTP/1.1\r\n\r\n"]]) await direct.getByLabel(label, { exact: true }).fill(value);
  await direct.getByLabel("WAF 조치", { exact: true }).selectOption("D");
  await direct.getByRole("button", { name: "추가 입력", exact: true }).click();
  await direct.getByLabel("스키마 추가 필드 (JSON)", { exact: true }).fill('{"vendor_score":2}');
  await page.getByRole("tab", { name: "배치 파일 분석", exact: true }).click();
  await page.getByRole("tab", { name: "단건 분석", exact: true }).click();
  assert.equal(await direct.getByLabel("회사명", { exact: true }).inputValue(), "Fixture");
  await direct.getByRole("button", { name: "분석 시작", exact: true }).click();
  assert.equal(await configure.isDisabled(), true);
  await page.waitForFunction(() => window.fixture.created.length === 1);
  assert.equal(await page.evaluate(() => window.fixture.writes[0].payload.candidate_configuration.primary_profile_id), "p2");
  assert.equal(await page.evaluate(() => window.fixture.writes[0].payload.event.vendor_score), 2);
  await page.getByRole("tab", { name: "배치 파일 분석", exact: true }).click();
  const file = page.getByRole("tabpanel", { name: "배치 파일 분석", exact: true });
  await file.locator('input[type="file"]').setInputFiles({ name: "fixture.json", mimeType: "application/json", buffer: Buffer.from("[]") });
  await file.getByRole("button", { name: "배치 분석 시작", exact: true }).click();
  await page.waitForFunction(() => window.fixture.created.length === 2);
  assert.equal(await page.evaluate(() => window.fixture.writes[1].payload.candidate_configuration.input_schema_version_id), "s2");
  await page.getByRole("tab", { name: "검증 데이터셋 분석", exact: true }).click();
  const dataset = page.getByRole("tabpanel", { name: "검증 데이터셋 분석", exact: true });
  await dataset.getByRole("button", { name: /미승인 fixture/ }).click();
  assert.equal(await dataset.getByRole("button", { name: "공식 평가 시작", exact: true }).isDisabled(), true);
  await dataset.getByLabel("데이터셋 검색", { exact: true }).fill("승인 fixture");
  await page.waitForFunction(() => window.fixture.reads.at(-1)?.query === '승인 fixture');
  await dataset.getByRole("button", { name: /^승인 fixture/ }).click();
  await dataset.getByLabel("데이터셋 검색", { exact: true }).press("Enter");
  assert.equal(await page.evaluate(() => window.fixture.writes.length), 2, "search Enter must not submit a paid analysis");
  await dataset.getByRole("button", { name: "공식 평가 시작", exact: true }).click();
  await page.waitForFunction(() => window.fixture.created.length === 3);
  assert.equal(await page.evaluate(() => window.fixture.writes[2].payload.evaluation_mode), "ground_truth");
  assert.equal(await page.evaluate(() => window.fixture.writes[2].payload.expected_revision), 8);
  assert.ok((await page.evaluate(() => window.fixture.writes)).every(call => call.payload.candidate_configuration.prompt_policy_version_id === 'v2'));
  await configure.click(); await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await dialog.evaluate(node => node.getBoundingClientRect().right <= innerWidth + 1), true);
  await page.keyboard.press("Escape");
  await page.evaluate(() => { window.fixture.catalogError = true; });
  await page.getByRole("button", { name: "기본 구성 다시 불러오기", exact: true }).click();
  await page.getByText("실행 구성을 불러오지 못했습니다. 다시 조회하세요.", { exact: true }).waitFor();
  assert.equal(await dataset.getByRole("button", { name: "공식 평가 시작", exact: true }).isDisabled(), true);
  await page.getByRole("button", { name: "결과 화면 전환", exact: true }).click();
  await page.getByText("평가 기록을 저장하는 중입니다. 잠시 후 새로고침됩니다.", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "평가 기록 저장", exact: true }).count(), 0);
  assert.equal(await page.getByRole("button", { name: "참고 답안 일괄 입력", exact: true }).count(), 0);
  await page.evaluate(() => { window.fixture.finalized = true; });
  await page.waitForFunction(() => [...document.querySelectorAll('option')].some(option => option.value === 'saved'));
  await page.getByRole("combobox", { name: "공식 평가 기록", exact: true }).selectOption("saved");
  await page.getByText("저장 당시 결과입니다. 이후 재실행 결과는 이 기록을 바꾸지 않습니다.", { exact: true }).waitFor();
  assert.deepEqual(errors, []); assert.equal(network, 0);
  console.log(JSON.stringify({ passed: ["candidate choices", "unverified disabled", "draft preserved", "three submissions pinned", "unapproved excluded", "search picker", "mobile dialog", "catalog failure blocks", "official snapshot polling", "frozen history controls"], actualApiCalls: 0, llmCalls: 0 }));
} finally { await browser.close(); }
