// Legacy command delegates to the current decision-v2 regression.
// Preserve this synthetic fixture block: other browser suites read it directly.
import { readFileSync } from "node:fs";
const id = "11111111-1111-4111-8111-111111111111", second = "22222222-2222-4222-8222-222222222222";
const shared = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/analyst_assessment_cases.json", import.meta.url), "utf8"))[0].result;
const mock = `
window.fixture={reads:[],writes:[],downloads:[],unknown:[],rawError:false,holdRaw:false,holdAgent:false};const state=window.fixture;
const result={...${JSON.stringify(shared)},verdict:'inconclusive',schema_version:'waf-analysis-v2',summary_ko:'인증 파라미터의 허용 형식을 확인해야 합니다.',
 diagnostics:{inconclusive_reasons:['verdict_disagreement']},threat_analysis:{severity:'HIGH',category:'SQL Injection',target:'payload.body',technique_ko:'인증 요청의 값과 처리 규칙을 확인합니다.',potential_impact_ko:'입력 검증 여부에 따라 다릅니다.'},
 analyst_guidance:{summary_ko:'인증 파라미터의 허용 형식을 확인해야 합니다.',checks:[{source_ko:'인증 서비스',check_ko:'파라미터 허용 형식을 확인하세요.',why_ko:'요청의 용도를 구분하기 위해 필요합니다.'}],limitations:[]},
 agent:{framework:'moduagent',role_profiles:{primary:{model_profile:'저장된 1차 모델'},verifier:{model_profile:'저장된 검증 모델'}}},verifier:{executed:true}};
result.analyst_assessment.evidence.push({evidence_id:'missing',field:'payload.body',excerpt:'missing-quote-canary',interpretation_ko:'이전 입력의 표현을 확인해야 합니다.',supports:'context'});
const base={id:'${id}',event_id:'hidden-event-canary',status:'completed',analysis_purpose:'test',ingest_channel:'test_lab',company_name:'가상 테스트 조직',src_ip:'192.0.2.10',dest_ip:'198.51.100.20',dest_port:443,src_port:45678,waf_action:'D',waf_vendor:'fixture',signature:'인증 요청 탐지',source_system:'fixture',model_profile:'저장된 1차 모델',prompt_version:'지침 v12',input_schema_metadata:{version_number:3,field_count:11},total_elapsed_ms:1500,queue_wait_ms:50,processing_duration_ms:1450,created_at:'2026-09-17T00:00:00Z',result,evaluation:{outcome:'unlabeled'}};
state.detail=base;
const raw=key=>({analysis_id:key,event_id:'hidden-event-canary',payload:key==='${id}'?'POST /auth HTTP/1.1\\r\\nHost: fixture.invalid\\r\\nCookie: private-fixture-cookie\\r\\n\\r\\ncode_verifier=fixture&encoded=%3Cscript%3E':'SECOND-INPUT-CANARY',extra_fields:{vendor_note:'<script>window.injected=true</script>'},decoding:{decoder_version:'fixture',items:[],warnings:[],scan_truncated:false}});
const runs=[{id:'hidden-run-canary',status:'completed',started_at:base.created_at,completed_at:base.created_at,duration_ms:1450,steps:[
 {id:'step-1',step_type:'llm_primary',status:'completed',duration_ms:700,output:{verdict:'inconclusive',nested:{number:3,flag:true}},input:{payload:'trace-input-canary'},metadata:{model_profile:'저장된 1차 모델',model_name:'fixture-model',output_validation_retry:{attempt_count:3},evidence_grounding_retry:{attempted:true,attempt_count:2,recovered:true,attempts:[{output_validation_retry:{attempt_count:3}},{output_validation_retry:{attempt_count:1}}]}}},
 {id:'step-2',step_type:'llm_verifier',status:'completed',duration_ms:600,output:{verdict:'inconclusive'},metadata:{model_profile:'저장된 검증 모델'}}]}];
const methods={me:async()=>({username:'fixture-admin',kind:'admin_session'}),dashboard:async()=>({counts:{pending:0,processing:0},runtime:{agent_mode:'moduagent'}}),
 analysis:async key=>key==='${id}'?structuredClone(state.detail):{...base,id:key,status:'failed',result:null,error_code:'primary_agent_failed'},
 evaluationLabels:async()=>({items:[]}),
 rawEvent:async key=>{if(state.rawError)throw Error('private-upstream-error');if(state.holdRaw)return new Promise(resolve=>{state.resolveRaw=()=>resolve(raw(key));});return raw(key);},
 agentRuns:async()=>{if(state.holdAgent)return new Promise(resolve=>{state.resolveAgent=()=>resolve(runs);});return runs;},
 referenceSelection:async ids=>({items:ids.map(analysis_id=>({analysis_id,expected_revision:0,verdict:null}))}),
 saveReferences:async body=>{state.writes.push(body);state.detail.evaluation={outcome:'expected_abstention_match',reference_label:{verdict:body.verdict,source_kind:'reference',ai_visible:true,revision:1}};return {applied_count:1};},
 validationDatasets:async()=>({items:[{id:'dataset-1',name:'검증용 데이터',revision:1,total:0}],total:1}),
 retryEligibility:async key=>({analysis_id:key,allowed:true,model_profile:'저장된 1차 모델',prompt_version:'지침 v12',provider:'vllm'}),
};
export const api=new Proxy(methods,{get(target,name){if(!target[name])return async()=>{state.unknown.push(name);throw Error('Unexpected mock API');};return async(...args)=>{state.reads.push({name,id:args[0]});return target[name](...args);};}});
window.fetch=async(path,options)=>{state.downloads.push({path,options});if(!/^\\/api\\/v1\\/analyses\\/[0-9a-f-]+\\/report\\.(pdf|xlsx)/.test(path))throw Error('Unexpected fetch');return new Response(path.includes('.pdf')?'%PDF-fixture':'PK-fixture',{headers:{'Content-Type':path.includes('.pdf')?'application/pdf':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}});};
`;
// Keep the shared historical fixture above for other offline browser suites.
// The current four-tab detail regression lives in the v2 suite.
await import("./analysis-decision-smoke.mjs");
