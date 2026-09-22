// Shared synthetic API fixture. No network or production data.
import { readFileSync } from "node:fs";
export function r5BrowserFixture() {
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
return mock;
}
