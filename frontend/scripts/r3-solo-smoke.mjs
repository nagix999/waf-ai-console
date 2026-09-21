// R3 React calibration + regression. Synthetic API fixtures only, offline.
// Reuses test data, never an older handoff or visual mockup.
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const source = fileURLToPath(new URL("../src", import.meta.url));
const output = process.env.SCREENSHOT_DIR || fileURLToPath(new URL("../../test-results/r3-solo", import.meta.url));
mkdirSync(output, { recursive: true });
const fixtures = readFileSync(new URL("./desktop-workspaces-smoke.mjs", import.meta.url), "utf8");
let mock = fixtures.split("const mock = `")[1].split("\n`;\n")[0].replaceAll("\\\\", "\\");
const detailId = "11111111-1111-4111-8111-111111111111";
const shared = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/analyst_assessment_cases.json", import.meta.url), "utf8"))[0].result;
const detailSource = readFileSync(new URL("./inference-detail-smoke.mjs", import.meta.url), "utf8").split("const mock = `")[1].split("export const api=")[0]
  .replace(/window\.fixture=.*?;const state=window\.fixture;/, "const state=window.fixture;")
  .replaceAll("${JSON.stringify(shared)}", JSON.stringify(shared)).replaceAll("${id}", detailId).replaceAll("\\\\", "\\")
  .replace("status:'failed',result:null,error_code:'primary_agent_failed'", "status:'completed'");
mock = mock.replace("export const api=", `Object.assign(methods, (()=>{${detailSource}; return Object.fromEntries(['analysis','rawEvent','agentRuns','evaluationLabels','retryEligibility'].map(key=>[key,methods[key]]));})());\nexport const api=`);
mock = mock.replace("export const api=", `
const snapshot={schema_version:1,primary:{profile_id:id(1),profile_fingerprint:hash},verifier:{profile_id:id(1),profile_fingerprint:hash},evidence_editor:{enabled:false,profile_id:null},prompt:{policy_version_id:id(3),prompt_version:'fixture/policy-1'},input_schema:{version_id:id(5),content_hash:hash}};
const production={configuration_id:id(77),configuration_hash:hash,snapshot,profile_names:{[id(1)]:'운영 모델',[id(2)]:'후보 모델'},version_names:{[id(3)]:'v1 · 운영 분석 지침',[id(5)]:'v1 · 기본 입력'},drifted:false};
runs.forEach(run=>{run.configuration_snapshot=snapshot;run.ground_truth={...run.ground_truth,published:true,dataset_id:id(7),dataset_revision_id:id(70),sample_count:3,comparison_key:'same-context'};});
const official={id:id(78),test_run_id:id(8),configuration_hash:hash,summary:runs[0],created_at:date};
const runtime={updated_at:date,window:'24h',production_configuration:production,health:{api:{status:'healthy',observed_at:date},analysis_worker:{status:'healthy',observed_at:date},model_worker:{status:'unknown'},assigned_models:{status:'unknown'}},queue:{pending:0,processing:0},outcome_summary:{completed:3,failed:0},request_count:3,retry_count:0,latency_summary:{p50:900,p95:1200},recent_analyses:[],request_volume_series:[],failure_types:[]};
const working={...dataset,working_revision:4,latest_published_revision_id:id(70),working_changes_count:2,changes:{added:1,changed:1,removed:0,unchanged:1},total:3,filtered_total:3,counts:{ready:1,needs_attention:1,excluded:1},published_revisions:[{id:id(70),revision:2,total:3,created_at:date,metadata:{working_total_at_publish:4,included_ready_count:3,needs_attention_count:1,excluded_count:0,inclusion_rate:.75}}],items:['ready','needs_attention','excluded'].map((state,n)=>({...item,id:id(20+n),item_id:id(20+n),state,excluded:state==='excluded',change:n===1?'added':n===0?'changed':'unchanged',case_name:['SQLi 경계 사례','인코딩 요청 확인','중복 요청 제외'][n],reference_verdict:n===1?null:'true_positive',tags:['regression'],issues:n===1?[{code:'reference_verdict_required'}]:[]}))};
Object.assign(methods,{
 overview:async()=>({production,evaluation:official,comparison_key:'same-context',trend:state.singlePoint?[official]:[{...official,id:id(79),created_at:'2026-09-16T00:00:00Z',summary:{...runs[1],evaluation_summary:{...evaluation,metrics:{...evaluation.metrics,f1:.82,accuracy:.89}}}},official],ground_truth_working_draft:working,recent_comparable_tests:[official],actions:[{kind:'ground_truth',count:1,dataset_id:id(7)},{kind:'promotion',test_run_id:id(8),name:'후보 모델 검증'}],updated_at:date}),
 workingDataset:async()=>working,workingItem:async(_,which)=>({...working.items.find(i=>i.item_id===which),event:item.event,comment:'원문 기반 참고 답안'}),
 saveWorkingItem:async(_,which,payload)=>{state.writes.push({name:'saveWorkingItem',payload});return {working_revision:5}},
 publishDataset:async(_,payload)=>{state.writes.push({name:'publishDataset',payload});return {id:id(71),revision:3}},
 productionConfiguration:async()=>({...production,evaluation:official}),productionEvaluations:async()=>({configuration:production,items:[official]}),
 runtimeStatus:async()=>runtime,activity:async()=>({items:[{id:id(80),category:'configuration',action:'publish_ground_truth_revision',actor:'fixture-admin',created_at:date,before:null,after:{revision:2}},{id:id(81),category:'promotion',action:'promote_production',actor:'fixture-admin',created_at:date,before:snapshot,after:snapshot}],total:2}),deployment:async()=>({db_revisions:['0023_ground_truth_working'],agent_mode:'moduagent',git_commit:null}),
 promotionPreflight:async run=>({candidate_test_run_id:run,candidate_name:'후보 모델 검증',candidate:{...snapshot,primary:{profile_id:id(2),profile_fingerprint:hash}},current:production,eligible:!state.incompatible,schema_changed:true,field_diff:[{field:'vendor_score',before:null,after:{type:'integer',required:true}}],checks:['candidate_snapshot_valid','candidate_completed_without_failures','official_approved_evaluation','published_membership_valid','official_snapshot_matches_candidate','tested_profiles_still_valid','tested_instructions_still_current'].map(code=>({code,passed:!state.incompatible})),evaluation:official,baseline_evaluation:official,comparable:!state.incompatible}),
 promoteConfiguration:async payload=>{state.writes.push({name:'promoteConfiguration',payload});return {id:id(90)}},
 logout:async()=>{state.loggedOut=true},me:async()=>{if(state.loggedOut)throw Error('logged out');return {username:'fixture-admin',kind:'admin_session',scopes:['admin']}}
});
export const api=`);
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: "jsx", resolveDir: source },
  plugins: [{ name: "offline-api", setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }],
  bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } }); page.setDefaultTimeout(12000);
  const errors = [], requests = [];
  page.on("pageerror", e => { errors.push(e.message); console.error("Browser error:", e.message); });
  await page.route("**/*", route => route.request().url() === "https://fixture.invalid/" ? route.fulfill({ contentType: "text/html", body: '<html lang="ko"><body><div id="root"></div></body></html>' }) : (requests.push(route.request().url()), route.abort()));
  await page.goto("https://fixture.invalid/");
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  const go = async hash => { await page.evaluate(hash => { location.hash = hash; }, hash); };
  const snap = async name => { await page.screenshot({ path: join(output, name + ".png"), fullPage: true }); };
  const theme = async value => { await page.getByRole("button", { name: /^(테마|Theme)$/, exact: true }).click(); await page.getByRole("button", { name: value, exact: true }).click(); };
  await page.locator(".r3-overview").waitFor();
  assert.equal(await page.locator(".console-sidebar nav > button").count(), 5);
  for (const color of ["SK", "Light", "Dark"]) {
    await theme(color);
    await go("#overview"); await page.locator(".r3-evaluation-chart").waitFor(); await snap("overview-" + color);
    await go("#evaluate/ground-truth"); await page.locator(".r3-ground-truth").waitFor(); await page.getByLabel("데이터셋", { exact: true }).selectOption('00000007-1111-4111-8111-111111111111');
    await page.getByRole("button", { name: "SQLi 경계 사례", exact: true }).click(); await page.getByLabel("문항명", { exact: true }).waitFor(); await snap("ground-truth-" + color);
    await go("#promotion/00000008-1111-4111-8111-111111111111"); await page.locator(".v5-preflight").waitFor();
    assert.equal(await page.locator('.console-sidebar [aria-current="page"]').count(), 0);
    assert.equal(await page.getByRole("button", { name: "Promote to Production", exact: true }).isDisabled(), true);
    await snap("promotion-" + color);
  }
  console.log("R3 calibration: Overview, Ground Truth, contextual Promotion / 3 themes passed.");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "EN", exact: true }).click();
  assert.equal(await page.getByRole("checkbox").isChecked(), true);
  await snap("promotion-EN-Dark");
  await page.getByRole("button", { name: "Promote to Production", exact: true }).evaluate(button => { button.click(); button.click(); });
  await page.getByRole("status").filter({ hasText: "Promoted to Production." }).waitFor();
  assert.equal((await page.evaluate(() => window.fixture.writes)).filter(row => row.name === "promoteConfiguration").length, 1);
  await page.getByRole("button", { name: "KR", exact: true }).click();
  await go("#promote"); await page.getByRole("button", { name: "평가지표 펼치기", exact: true }).waitFor();
  await page.getByRole("button", { name: "평가지표 펼치기", exact: true }).click();
  assert.equal(await page.getByRole("button", { name: "평가지표 접기", exact: true }).getAttribute("aria-expanded"), "true");
  await snap("tests-expanded-Dark");
  await page.getByRole("button", { name: "후보 모델 검증", exact: true }).click();
  await page.getByRole("button", { name: "예시 요청 1", exact: true }).click();
  await page.locator(".r3-case-drawer[open]").waitFor();
  await page.screenshot({ path: join(output, "test-case-drawer-Dark.png"), fullPage: false });
  assert.match(new URL(page.url()).hash, /\/case\//);
  await page.getByRole("button", { name: "전체 분석 상세", exact: false }).click();
  await page.locator(".inference-detail").waitFor();
  assert.equal(await page.locator('.inference-detail > [role="tablist"] [role="tab"]').count(), 5);
  await page.goBack();
  await page.locator(".r3-case-drawer[open]").waitFor();
  assert.match(new URL(page.url()).hash, /\/case\//);
  await page.keyboard.press("Escape"); await page.locator(".r3-case-drawer[open]").waitFor({ state: "hidden" });
  await page.goBack(); await page.getByRole("button", { name: "평가지표 접기", exact: true }).waitFor();
  for (const color of ["SK", "Light", "Dark"]) {
    await theme(color);
    for (const [hash, selector] of [["#configure/llm-profiles", ".profile-section"], ["#configure/agent-roles", ".agent-settings"], ["#configure/instructions", ".prompt-settings"], ["#operate/runtime", ".agent-settings-panel"], ["#operate/inference", ".analysis-list-toolbar"], ["#operate/activity", ".v5-workbench"], ["#connect/production-api", ".api-document-page"], ["#connect/api-keys", ".service-api-keys"], ["#connect/input-schema", ".input-schema-settings"], ["#connect/vllm-targets", ".internal-egress-settings"]]) {
      await go(hash); await page.locator(selector).first().waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, color + hash);
      const tab = page.locator('.v5-workspace-tabs button[aria-current="page"]');
      if (await tab.count()) { await tab.focus(); const foreground = await tab.evaluate(el => getComputedStyle(el).color); if (color !== "Dark") assert.notEqual(foreground, "rgb(255, 255, 255)", color + hash); }
      await snap(color + hash.replaceAll(/[\/#]/g, "-"));
    }
  }
  await go("#evaluate/ground-truth");
  await page.getByLabel("데이터셋", { exact: true }).selectOption('00000007-1111-4111-8111-111111111111');
  await page.getByRole("button", { name: "리비전 발행", exact: true }).click();
  const publishDialog = page.getByRole("dialog", { name: "리비전 발행", exact: true });
  assert.equal(await publishDialog.getByRole("button", { name: "발행", exact: true }).isDisabled(), true);
  await publishDialog.getByRole("checkbox").check();
  assert.equal(await publishDialog.getByRole("checkbox").isChecked(), true);
  await publishDialog.getByRole("button", { name: "발행", exact: true }).click();
  await publishDialog.waitFor({ state: "hidden" });
  await theme("Light");
  assert.equal((await page.evaluate(() => window.fixture.writes)).filter(row => row.name === "publishDataset").length, 1);
  await snap("ground-truth-published-Light");
  await page.evaluate(() => { window.fixture.singlePoint = true; });
  await go("#overview"); await page.locator(".r3-overview").waitFor();
  await page.getByText("현재 공식 평가", { exact: true }).waitFor();
  assert.equal(await page.locator(".r3-evaluation-chart").count(), 0);
  await snap("overview-single-context-Light");
  await page.getByRole("button", { name: "계정", exact: true }).click();
  await page.getByRole("button", { name: "로그아웃", exact: true }).click();
  await page.locator(".login-card").waitFor();
  for (const color of ["SK", "Light", "Dark"]) { await theme(color); await snap("login-" + color); }
  await page.getByRole("button", { name: "EN", exact: true }).click();
  await page.getByLabel("Username", { exact: true }).fill("fixture-user");
  await theme("Light");
  assert.equal(await page.getByLabel("Username", { exact: true }).inputValue(), "fixture-user");
  await snap("login-EN-Light");
  assert.deepEqual(errors, []);
  assert.deepEqual(requests, []);
  assert.deepEqual(await page.evaluate(() => window.fixture.unknown), []);
  console.log("R3 browser checks passed; screenshots: " + output);
} finally { await browser.close(); }
