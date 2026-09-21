// Synthetic, offline browser QA. No real DB, model, credentials or paid calls.
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const source = fileURLToPath(new URL("../src", import.meta.url));
const output = process.env.SCREENSHOT_DIR || fileURLToPath(new URL("../../test-results/canonical-v5", import.meta.url));
mkdirSync(output, { recursive: true });
const existing = readFileSync(new URL("./desktop-workspaces-smoke.mjs", import.meta.url), "utf8");
// Reuse the existing explicit synthetic API fixture, without running its tests.
let mock = existing.split("const mock = `")[1].split("\n`;\n")[0].replaceAll("\\\\", "\\");
const detailId = "11111111-1111-4111-8111-111111111111";
const shared = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/analyst_assessment_cases.json", import.meta.url), "utf8"))[0].result;
const detailSource = readFileSync(new URL("./inference-detail-smoke.mjs", import.meta.url), "utf8").split("const mock = `")[1].split("export const api=")[0]
  .replace(/window\.fixture=.*?;const state=window\.fixture;/, "const state=window.fixture;")
  .replaceAll("${JSON.stringify(shared)}", JSON.stringify(shared)).replaceAll("${id}", detailId).replaceAll("\\\\", "\\");
mock = mock.replace("export const api=", `Object.assign(methods, (()=>{${detailSource}; return Object.fromEntries(['analysis','rawEvent','agentRuns','evaluationLabels','retryEligibility'].map(key=>[key,methods[key]]));})());\nexport const api=`);
mock = mock.replace("export const api=", `
const snapshot={schema_version:1,primary:{profile_id:id(1),profile_fingerprint:hash},verifier:{profile_id:id(1),profile_fingerprint:hash},evidence_editor:{enabled:false,profile_id:null},prompt:{policy_version_id:id(3),prompt_version:'waf-judgment-fixture/policy-1'},input_schema:{version_id:id(5),content_hash:hash}};
const production={configuration_id:id(77),configuration_hash:hash,snapshot,profile_names:{[id(1)]:'운영 모델',[id(2)]:'후보 모델'},drifted:false};
const official={id:id(78),test_run_id:id(8),configuration_hash:hash,summary:runs[0],created_at:date};
const runtime={updated_at:date,window:'24h',production_configuration:production,health:{api:{status:'healthy',observed_at:date},analysis_worker:{status:'healthy',observed_at:date},model_worker:{status:'unknown',observed_at:null},assigned_models:{status:'unknown'}},queue:{pending:0,processing:0},outcome_summary:{completed:3,failed:0,pending:0,processing:0},request_count:3,retry_count:0,latency_summary:{p50:900,p95:1200},recent_analyses:[]};
runtime.request_volume_series=Array.from({length:12},(_,i)=>({from:new Date(Date.parse(date)+i*7200000).toISOString(),completed:i%3,failed:i===2?1:0,pending:0,processing:0,retry:0}));runtime.failure_types=[];
official.summary={...official.summary,breakdowns:{test_category:[{name:'SQLi',evaluation_summary:evaluation}],difficulty:[{name:'hard',evaluation_summary:evaluation}]}};
Object.assign(methods,{
 productionConfiguration:async()=>({...production,evaluation:official}), productionEvaluations:async()=>({configuration:production,items:[official]}),
 runtimeStatus:async()=>runtime,activity:async()=>({items:[{id:id(80),category:'promotion',action:'promote_production',actor:'fixture-admin',created_at:date,before:snapshot,after:snapshot}],total:1}),deployment:async()=>({db_revisions:['0022_production_lifecycle'],agent_mode:'moduagent',git_commit:null}),
 promotionPreflight:async run=>({candidate_test_run_id:run,candidate_name:'후보 모델 검증',candidate:{...snapshot,primary:{profile_id:id(2),profile_fingerprint:hash}},current:production,eligible:!state.incompatible,schema_changed:true,field_diff:[{field:'vendor_score',before:null,after:{type:'integer',required:true}}],checks:['candidate_snapshot_valid','candidate_completed_without_failures','official_approved_evaluation','approved_membership_valid','official_snapshot_matches_candidate','tested_profiles_still_valid','tested_instructions_still_current'].map(code=>({code,passed:!state.incompatible})),evaluation:official,baseline_evaluation:official,comparable:true}),
 promoteConfiguration:async payload=>{state.writes.push({name:'promoteConfiguration',payload});return {id:id(90)}}
});
export const api=`);
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: "jsx", resolveDir: source },
  plugins: [{ name: "offline-api", setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }],
  bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.setDefaultTimeout(10000);
  const errors = [], requests = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.route("**/*", route => route.request().url() === "https://fixture.invalid/" ? route.fulfill({ contentType: "text/html", body: '<html lang="ko"><body><div id="root"></div></body></html>' }) : (requests.push(route.request().url()), route.abort()));
  await page.goto("https://fixture.invalid/");
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  await page.locator(".v5-overview").waitFor();
  assert.equal(await page.locator("html").getAttribute("data-theme"), "sk");
  assert.equal(await page.locator(".console-sidebar nav > button").count(), 6);
  for (const theme of ["sk", "light", "dark"]) {
    await page.getByRole("button", { name: "테마", exact: true }).click();
    await page.getByRole("button", { name: theme === "sk" ? "SK" : theme === "light" ? "Light" : "Dark", exact: true }).click();
    await page.screenshot({ path: join(output, `overview-${theme}-1440.png`) });
  }
  await page.evaluate(() => { location.hash = '#evaluate/ground-truth/00000007-1111-4111-8111-111111111111'; });
  await page.getByRole("button", { name: "SQLi 경계 사례", exact: true }).waitFor();
  await page.getByRole("button", { name: "SQLi 경계 사례", exact: true }).click();
  await page.locator(".v5-ground-truth-workbench.has-detail").waitFor();
  assert.equal(await page.locator("dialog[open]").count(), 0);
  await page.screenshot({ path: join(output, "ground-truth-dark-1440.png") });
  await page.getByLabel("문항명",{exact:true}).fill("UNSAVED-FIXTURE-DRAFT");
  assert.equal(await page.getByRole("button",{name:"새로고침",exact:true}).isDisabled(),true);
  assert.equal(await page.getByRole("button",{name:"문항 추가",exact:true}).isDisabled(),true);
  await page.getByRole("button",{name:"EN",exact:true}).click();
  assert.equal(await page.getByLabel("문항명",{exact:true}).inputValue(),"UNSAVED-FIXTURE-DRAFT");
  await page.getByRole("button",{name:"KR",exact:true}).click();
  await page.getByRole("button",{name:"검증 문항 닫기",exact:true}).click();
  await page.getByRole("dialog",{name:"수정 내용 버리기",exact:true}).getByRole("button",{name:"수정 내용 버리기",exact:true}).click();
  await page.evaluate(() => { location.hash = '#promote'; });
  await page.locator(".v5-promotion select").selectOption('00000008-1111-4111-8111-111111111111');
  const promote = page.getByRole("button", { name: "Promote to Production", exact: true });
  await page.locator(".v5-preflight").waitFor();
  assert.equal(await promote.isDisabled(), true);
  await page.getByRole("checkbox").check();
  assert.equal(await promote.isEnabled(), true);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: join(output, "promote-dark-1440.png") });
  await page.getByRole("button", { name: "EN", exact: true }).click();
  assert.equal(await page.locator(".v5-promotion select").inputValue(), '00000008-1111-4111-8111-111111111111');
  assert.equal(await page.getByRole("checkbox").isChecked(), true);
  await page.setViewportSize({ width:1600,height:1000 });
  await page.screenshot({ path: join(output, "promote-en-dark-1600.png") });
  await promote.evaluate(button => { button.click(); button.click(); });
  await page.getByRole("status").filter({ hasText: "Promoted to Production." }).waitFor();
  assert.equal((await page.evaluate(() => window.fixture.writes)).filter(r => r.name === 'promoteConfiguration').length, 1);
  await page.evaluate(() => { window.fixture.incompatible=true; location.hash='#overview'; });
  await page.locator('.v5-overview').waitFor();
  await page.evaluate(() => { location.hash='#promote'; });
  await page.locator('.v5-promotion select').selectOption('00000008-1111-4111-8111-111111111111');
  await page.locator('.v5-preflight').waitFor();
  await page.getByRole('checkbox').check();
  assert.equal(await page.getByRole('button',{name:'Promote to Production',exact:true}).isDisabled(),true);
  await page.evaluate(() => { window.fixture.incompatible=false; });
  // All workspaces use the same primitives; inspect actual React, not the HTML mockups.
  await page.getByRole("button", { name: "KR", exact: true }).click();
  for (const theme of ["sk", "light", "dark"]) {
    await page.getByRole("button", { name: "테마", exact: true }).click();
    await page.getByRole("button", { name: ({sk:"SK",light:"Light",dark:"Dark"})[theme], exact:true }).click();
    for (const [route, name] of [["overview","overview"],["evaluate/ground-truth/00000007-1111-4111-8111-111111111111","ground-truth"],["promote","promote"]]) {
      await page.evaluate(route => { location.hash = '#' + route; window.scrollTo(0,0); }, route);
      await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
      if (name === "ground-truth") { await page.getByRole("button", { name: "SQLi 경계 사례", exact:true }).click(); await page.locator(".v5-workbench-detail .gt-editor").waitFor(); }
      if (name === "promote") { await page.locator(".v5-promotion select").selectOption('00000008-1111-4111-8111-111111111111'); await page.locator(".v5-preflight").waitFor(); }
      await page.screenshot({path:join(output,`${name}-${theme}-1600.png`)});
    }
  }
  const screens = [
    ["configure/llm-profiles","profiles"], ["configure/agent-roles","roles"], ["configure/instructions","instructions"],
    ["evaluate/tests","tests"], ["evaluate/tests/new","new-test"], ["evaluate/ground-truth","datasets"], ["evaluate/production-evaluation","production-evaluation"],
    ["operate/runtime","runtime"], ["operate/inference","inference"], ["operate/activity","activity"],
    ["connect/production-api","production-api"], ["connect/api-keys","api-keys"], ["connect/input-schema","input-schema"], ["connect/vllm-targets","targets"],
    ...["result","agent-trace","input","result-json","report"].map(tab => [`operate/inference/${detailId}/${tab}`,`detail-${tab}`]),
  ];
  await page.getByRole("button", { name:"테마",exact:true }).click();
  await page.getByRole("button", { name:"SK",exact:true }).click();
  for (const [route,name] of screens) {
    await page.evaluate(route => { location.hash = '#'+route; window.scrollTo(0,0); },route);
    await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
    assert.equal(await page.locator(".console-sidebar nav > button").count(),6,name);
    if (name.startsWith("detail-")) await page.getByRole("tablist",{name:"분석 상세",exact:true}).waitFor();
    if (name === "detail-agent-trace") await page.locator(".inspection-repair-summary").waitFor();
    if (name === "detail-input") await page.locator(".raw-event-view").waitFor();
    if (["profiles","api-keys","targets"].includes(name)) {
      const search = page.locator(".registry-search input");
      await search.fill("NO-MATCH-PRIVATE-QUERY");
      assert.ok(!(await page.url()).includes("NO-MATCH"));
      if (name === "profiles") await page.getByText("검색 조건에 맞는 모델이 없습니다.",{exact:true}).waitFor();
      if (name === "targets") await page.getByText("검색 조건에 맞는 대상이 없습니다.",{exact:true}).waitFor();
      await page.getByRole("button",{name:"EN",exact:true}).click();
      assert.equal(await search.inputValue(),"NO-MATCH-PRIVATE-QUERY");
      await page.getByRole("button",{name:"KR",exact:true}).click();
      await search.fill("");
    }
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2),`horizontal overflow: ${name}`);
    await page.mouse.move(0,0);
    await page.screenshot({path:join(output,`${name}-sk-1600.png`)});
    if (name === "roles") assert.equal(await page.locator("fieldset").filter({has:page.locator("legend",{hasText:"Production"})}).getAttribute("disabled"),"");
    if (name === "instructions") assert.equal(await page.getByRole("button", {name:/^(공통 적용|이 버전으로 복귀)$/}).count(),0);
  }
  // In-memory form draft survives appearance changes. Nothing enters the URL.
  await page.evaluate(() => { location.hash='#configure/llm-profiles'; });
  await page.getByRole("button",{name:"모델 추가",exact:true}).click();
  await page.getByLabel("프로필 이름",{exact:true}).fill("draft-must-stay-private");
  await page.getByRole("button",{name:/모델 추가 닫기/}).click();
  await page.getByRole("button",{name:"EN",exact:true}).click();
  await page.getByRole("button",{name:"Theme",exact:true}).click();
  await page.getByRole("button",{name:"Dark",exact:true}).click();
  await page.getByRole("button",{name:"모델 추가",exact:true}).click();
  assert.equal(await page.getByLabel("프로필 이름",{exact:true}).inputValue(),"draft-must-stay-private");
  assert.ok(!page.url().includes("draft-must-stay-private"));
  assert.equal(await page.evaluate(() => Object.values(localStorage).some(value => value.includes("draft-must-stay-private"))),false);
  await page.screenshot({path:join(output,"profile-draft-en-dark-1600.png")});
  assert.deepEqual(errors, []); assert.deepEqual(requests, []);
  assert.deepEqual(await page.evaluate(()=>window.fixture.unknown), []);
  console.log(JSON.stringify({ screenshots:output, passed:["six workspaces", "SK default", "three themes", "GT workbench", "schema acknowledgement", "locale preserves selection", "double-click guard"], apiCalls:0,llmCalls:0 }));
} finally { await browser.close(); }
