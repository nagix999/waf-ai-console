// R5 offline React workflows, themes, locales and accessibility. Synthetic API fixtures only, offline.
// Reuses test data, never an older handoff or visual mockup.
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const source = fileURLToPath(new URL("../src", import.meta.url));
const output = process.env.SCREENSHOT_DIR || fileURLToPath(new URL("../../test-results/r5-final", import.meta.url));
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

mock = mock.replace("export const api=", `
const defaults={...role,prompt_policy_version_id:id(3),input_schema_version_id:id(5)};
runs.forEach(run=>run.test_purpose='official_evaluation');
working.included_reference_origin_counts={manual:1};
working.items.forEach(row=>row.reference_origin='manual');
const oldOverview=methods.overview,oldAnalysis=methods.analysis;state.productionState='promoted';
const unconfiguredProduction={...production,configuration_id:null,source_test_run_id:null,profile_names:{},version_names:{},snapshot:{...snapshot,primary:{profile_id:null},verifier:{profile_id:null},prompt:{policy_version_id:null},input_schema:{version_id:null}}};
Object.assign(methods,{
 testDefaults:async()=>({candidate_configuration:defaults,revision:1,valid:true}),
 saveTestDefaults:async payload=>{state.writes.push({name:'saveTestDefaults',payload});return {candidate_configuration:payload.candidate_configuration,revision:2,valid:true}},
 cloneTest:async()=>({candidate_configuration:defaults,test_purpose:'official_evaluation',source_dataset_id:id(7),source_dataset_revision_id:id(70),latest_published_dataset_revision_id:id(71),resource_validity:{valid:true}}),
 overview:async()=>({...await oldOverview(),...(state.productionState!=='promoted'?{production:state.productionState==='unconfigured'?unconfiguredProduction:{...production,configuration_id:null},evaluation:null,comparison_key:null,trend:[],ground_truth_working_draft:null,recent_comparable_tests:[],actions:state.productionState==='unconfigured'?[{kind:'promotion',test_run_id:id(8),name:'공식 테스트'}]:[]}:{}),setup:{production_state:state.productionState,steps:Object.fromEntries(['model_connection','test_configuration','ground_truth_published','official_candidate_test','first_promotion'].map(key=>[key,{state:key==='first_promotion'?'not_started':'ready',test_run_id:id(8)}])),production_api_credentials:'ready',production_api_traffic:{status:'not_observed'}}}),
 runtimeStatus:async()=>state.productionState==='unconfigured'?{...runtime,production_configuration:unconfiguredProduction,health:{analysis_worker:{status:'unknown'}},queue:{pending:0,processing:0},request_count:0,outcome_summary:{completed:0,failed:0},latency_summary:{p95:null}}:runtime,
 analysis:async value=>({...await oldAnalysis(value),id:value,analysis_purpose:value==='11111111-1111-4111-8111-111111111111'?'production':'test',test_run_id:value==='11111111-1111-4111-8111-111111111111'?null:id(8),initial_assessment:value==='11111111-1111-4111-8111-111111111111'?{verdict:'false_positive',probability:.91,comparison:'final_inconclusive',model_version:'fixture-initial'}:null}),
 analyses:async query=>{state.searchQuery=query;return {items:[{id:'11111111-1111-4111-8111-111111111111',event_id:'fixture-event',signature:'인코딩 요청 검사',analysis_purpose:'production',status:'completed',verdict:'true_positive',severity:'HIGH',summary_ko:'예시 분석 요약',created_at:date,initial_assessment:{verdict:'false_positive',probability:.91,comparison:'different'}}],total:1,evaluation_summary:evaluation}},
 previewTestImport:async(_,payload)=>{state.writes.push({name:'previewTestImport',payload});return {preview_token:id(91),source_total:3,importable:3,new_count:2,duplicate_count:1,reference_conflict_count:0,missing_reference_count:1,unavailable_count:0,items:cases.map((row,i)=>({test_run_item_id:row.id,row_number:i+1,case_name:row.case_name,category:i===0?'new':i===1?'duplicate':'missing_reference'}))}},
 confirmTestImport:async(_,payload)=>{state.writes.push({name:'confirmTestImport',payload});return {dataset_id:id(7),working_revision:5,added:2,duplicates:1,conflicts:0,published:false}},
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
  const page=await browser.newPage({viewport:{width:1440,height:900}});page.setDefaultTimeout(10000);
  const errors=[], requests=[];page.on("pageerror",e=>{errors.push(e.message);console.error(e.message)});
  await page.route("**/*",r=>r.request().url()==="https://fixture.invalid/"?r.fulfill({contentType:"text/html",body:'<html lang="ko"><body><div id="root"></div></body></html>'}):(requests.push(r.request().url()),r.abort()));
  await page.goto("https://fixture.invalid/");
  await page.addStyleTag({content:bundle.outputFiles.find(f=>f.path.endsWith(".css")).text});
  await page.addScriptTag({content:bundle.outputFiles.find(f=>f.path.endsWith(".js")).text});
  const go=async hash=>{await page.evaluate(hash=>{location.hash=hash},hash);};
  const snap=async name=>{await page.evaluate(()=>{scrollTo(0,0)});await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));await page.screenshot({path:join(output,name+".png"),fullPage:true})};
  const theme=async value=>{await page.getByRole("button",{name:/^(테마|Theme)$/,exact:true}).click();await page.getByRole("button",{name:value,exact:true}).click();};
  const run='00000008-1111-4111-8111-111111111111';
  await page.locator(".r3-overview").waitFor();
  for(const color of ["SK","Light","Dark"]){
    await theme(color);
    for(const locale of ["KR","EN"]){
      await page.getByRole("button",{name:locale,exact:true}).click();
      await go("#overview");await page.locator(".r3-evaluation-chart").waitFor();await snap("home-"+locale+"-"+color);
      await go("#evaluate/tests/"+run);await page.locator(".test-run-detail").waitFor();
      await page.getByRole("tab",{name:locale==="KR"?"평가 상세":"Evaluation Details",exact:true}).click();
      await page.locator(".r5-matrix").waitFor();assert.equal(await page.locator(".r5-matrix-cell").count(),4);
      assert.equal(await page.locator(".test-run-detail > .v5-metric-strip").count(),0);
      await snap("test-evaluation-"+locale+"-"+color);
      const focused=page.getByRole("tab",{name:locale==="KR"?"평가 상세":"Evaluation Details",exact:true});await focused.focus();
      if(color!=="Dark")assert.notEqual(await focused.evaluate(el=>getComputedStyle(el).color),"rgb(255, 255, 255)");
      await page.getByRole("button",{name:locale==="KR"?"운영 반영 검토 →":"Production Review →",exact:true}).click();
      await page.locator(".v5-preflight").waitFor();await snap("production-review-"+locale+"-"+color);
      assert.equal(await page.locator(".brand-mutation").isDisabled(),true);
      await go("#evaluate/ground-truth");await page.getByLabel(locale==="KR"?"데이터셋":"Dataset",{exact:true}).selectOption("00000007-1111-4111-8111-111111111111");
      await page.getByRole("button",{name:"SQLi 경계 사례",exact:true}).click();await page.locator(".r3-draft-editor").waitFor();await snap("ground-truth-"+locale+"-"+color);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,locale+color);
    }
  }
  await page.getByRole("button",{name:"KR",exact:true}).click();await theme("SK");
  await go("#evaluate/tests");await page.getByRole("button",{name:"기본 테스트 설정",exact:true}).click();
  await page.getByRole("dialog").waitFor();await page.getByRole("button",{name:"기본 설정 저장",exact:true}).waitFor();
  await snap("default-test-configuration");await page.getByRole("button",{name:"기본 설정 저장",exact:true}).click();await page.getByRole("status").filter({hasText:"기본 테스트 설정을 저장"}).waitFor();await page.keyboard.press("Escape");
  assert.equal(await page.getByRole("button",{name:"기본 테스트 설정",exact:true}).evaluate(el=>el===document.activeElement),true);
  await page.getByRole("button",{name:"+ 새 테스트",exact:true}).click();await page.locator(".r5-configuration-form").waitFor();await snap("new-official-test");
  await page.getByRole("radio",{name:/개발 테스트/}).check();await page.getByRole("tab",{name:"단건 분석",exact:true}).waitFor();
  await page.locator(".form-panel .test-name-field input").fill("fixture retained draft");
  await page.getByRole("button",{name:"EN",exact:true}).click();assert.equal(await page.getByRole("radio",{name:/Development Test/}).isChecked(),true);
  assert.equal(await page.locator(".form-panel .test-name-field input").inputValue(),"fixture retained draft");
  const directTab=page.getByRole("tab",{name:"Single Analysis",exact:true});await directTab.focus();await page.keyboard.press("ArrowRight");
  assert.equal(await directTab.getAttribute("aria-selected"),"true");await page.keyboard.press("Enter");
  await page.getByRole("tabpanel").filter({has:page.locator(".upload-panel")}).waitFor({state:"visible"});
  await page.getByRole("button",{name:"KR",exact:true}).click();
  await go("#evaluate/tests/"+run);await page.getByRole("button",{name:"이 설정으로 다시 테스트",exact:true}).click();await page.locator(".r5-configuration-form").waitFor();
  await page.getByText("더 최신 공식 버전이 있지만 이전 테스트의 버전을 유지했습니다.").waitFor();await snap("clone-pinned-version");
  await go("#evaluate/tests/"+run);await page.getByRole("button",{name:"테스트 작업",exact:true}).click();
  await page.getByRole("button",{name:"테스트 사례를 정답 데이터에 추가",exact:true}).click();await page.getByRole("button",{name:"미리보기",exact:true}).click();
  await page.locator(".r5-import-summary").waitFor();await snap("import-preview");
  await page.getByRole("button",{name:"편집 중 데이터에 추가",exact:true}).click();await page.getByRole("button",{name:"정답 데이터 열기",exact:true}).click();
  await page.locator(".r3-ground-truth").waitFor();
  await go("#evaluate/tests/"+run);await page.getByRole("button",{name:"예시 요청 1",exact:true}).click();await page.locator(".r3-case-drawer[open]").waitFor();
  await page.getByRole("button",{name:/크게 보기/}).click();await page.locator(".inference-detail").waitFor();
  assert.match(new URL(page.url()).hash,/^#evaluate\/tests\/.*\/case\/.*\/result$/);
  await page.goBack();await page.locator(".r3-case-drawer[open]").waitFor();await page.keyboard.press("Escape");
  await go("#operate/inference");await page.locator(".analysis-data-table").waitFor();
  assert.equal(await page.evaluate(()=>window.fixture.searchQuery.analysis_purpose),"production");
  await snap("production-inference");
  await page.getByRole("button",{name:"인코딩 요청 검사",exact:true}).click();await page.locator(".r5-initial").waitFor();await snap("initial-deep-comparison");
  await go("#promotion/"+run);await page.evaluate(()=>{window.fixture.incompatible=true});await page.getByRole("button",{name:"다시 확인",exact:true}).click();
  await page.locator(".v5-preflight").waitFor();assert.equal(await page.locator(".brand-mutation").isDisabled(),true);await snap("production-review-blocked");
  for(const state of ["unconfigured","legacy_active"]){await page.evaluate(state=>{window.fixture.productionState=state},state);await go("#overview");await page.locator(state==="unconfigured"?".r5-setup":".r3-overview").waitFor();await snap("home-"+state);await go("#evaluate/tests");}
  for(const [route,selector] of [["#configure/llm-profiles",".profile-section"],["#configure/agent-roles",".r5-readonly-roles"],["#configure/instructions",".prompt-settings"],["#configure/input-schema",".input-schema-settings"],["#operate/runtime",".agent-settings-panel"],["#operate/activity",".v5-workbench"],["#connect/production-api",".api-document-page"],["#connect/api-keys",".service-api-keys"],["#connect/vllm-targets",".internal-egress-settings"]]){await go(route);await page.locator(selector).first().waitFor();await snap(route.replaceAll(/[\/#]/g,"-"));assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,route);}
  await page.emulateMedia({reducedMotion:"reduce"});
  for(const width of [1280,1024]){await page.setViewportSize({width,height:900});await go("#evaluate/tests/"+run);await page.locator(".test-run-header").waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,"narrow "+width);await snap("test-detail-"+width);}
  await page.setViewportSize({width:1440,height:900});await page.evaluate(()=>{window.fixture.incompatible=false});
  await go("#promotion/"+run);await page.locator(".v5-preflight").waitFor();
  assert.equal(await page.locator(".brand-mutation").isDisabled(),true);
  await page.getByRole("checkbox",{name:"필드 변경과 수집기 영향을 확인했습니다.",exact:true}).check();
  await page.getByRole("button",{name:"EN",exact:true}).click();
  assert.equal(await page.getByRole("checkbox",{name:"I reviewed the field changes and client impact.",exact:true}).isChecked(),true);
  await page.locator(".brand-mutation").evaluate(el=>{el.click();el.click()});
  await page.waitForFunction(()=>window.fixture.writes.some(row=>row.name==="promoteConfiguration"));
  assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);assert.deepEqual(await page.evaluate(()=>window.fixture.unknown),[]);
  const writes=await page.evaluate(()=>window.fixture.writes);assert.equal(writes.filter(r=>r.name==="saveTestDefaults").length,1);assert.equal(writes.filter(r=>r.name==="confirmTestImport").length,1);assert.equal(writes.filter(r=>r.name==="promoteConfiguration").length,1);
  console.log("R5: themes × locales, inline evaluation, defaults, pinned clone, preview/confirm, Production scope, case/back/focus and screenshots passed. No network or real data.");
} finally {await browser.close();}
