// Isolated component/browser UX checks. Every API is intercepted; no model,
// live reference answer or deployment data can be read or changed by this test.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdtempSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const origin = "http://127.0.0.1:15173";
const archive = join(homedir(), ".cache/uv/archive-v0");
const modulePath = readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
if (!modulePath) throw new Error("Existing Playwright required");
const { chromium } = await import(pathToFileURL(modulePath).href);
const browser = await chromium.launch({ executablePath: join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
const output = mkdtempSync(join(tmpdir(), "waf-reference-report-"));
const reference = { id: "hidden-reference-uuid", verdict: "true_positive", source_kind: "synthetic_expected", ai_visible: false, source_ref: "검토_1차", revision: 2, created_at: "2026-09-08T00:00:00Z", created_by: "<img src='https://blocked.invalid/x'>" };
const detail = { id: "hidden-analysis-uuid", event_id: "hidden-event-uuid", source_system: "fixture-source", status: "completed", analysis_purpose: "test", ingest_channel: "test_lab", company_name: "테스트 회사", src_ip: "192.0.2.1", dest_ip: "198.51.100.1", src_port: 0, dest_port: 443, signature: "요청 경로 점검", created_at: reference.created_at, total_elapsed_ms: 1200, evaluation: { outcome: "match", reference_label: reference }, result: { verdict: "true_positive", schema_version: "waf-analysis-v2", summary_ko: "요청 경로에서 상위 디렉터리 접근을 확인했습니다.", threat_analysis: { severity: "HIGH", technique_ko: "상위 경로 접근", potential_impact_ko: "파일 내용이 노출될 수 있습니다." }, evidence: [{ field: "payload.path", excerpt: "../example\r\n<img src=https://blocked.invalid/excerpt>", interpretation_ko: "요청 경로의 상위 디렉터리 접근은 파일 노출 위험과 연결됩니다." }], analyst_guidance: { checks: [], limitations: [] }, agent: { framework: "moduagent" } } };
const group = { source_kind: "synthetic_expected", ai_visible: false, matches: 1, evaluable: 2, binary_decided: 2, binary_evaluable: 3, false_negatives: 1, false_positives: 0, abstained: 1, expected_abstention_matches: 1, expected_abstention_mismatches: 2 };
const summary = { total: 9, labeled: 8, evaluable: 4, matches: 1, binary_evaluable: 3, binary_decided: 2, support_positive: 2, support_negative: 1, label_coverage: 8 / 9, source_groups: [group], outcomes: { match: 1, false_negative: 1, abstained: 1, expected_abstention_match: 1, expected_abstention_mismatch: 2, failed: 1, pending: 1, unlabeled: 1 }, confusion_matrix: { tp: 0, fn: 1, fp: 0, tn: 1, abstained_positive: 1, abstained_negative: 0 }, metrics: { accuracy: .5, precision: null, recall: 0, f1: 0, coverage: 2 / 3, abstention_rate: 1 / 3 } };
const testRun = { id: "fixture-run", name: "검토용 테스트", source_system: "fixture-source", kind: "upload", status: "completed", created_at: reference.created_at, total: 1, accepted: 1, rejected: 0, pending: 0, processing: 0, completed: 1, failed: 0, total_elapsed_ms: 1200, execution_mode: "moduagent", profile_metadata: { model_name: "fixture-model" }, prompt_version: "fixture-prompt", evaluation_summary: summary, items: [], total_items: 0, facets: { difficulties: [], test_categories: [] }, missing_difficulty_count: 1, missing_test_category_count: 1 };
const fixture = JSON.stringify({ detail, summary, reference }).replaceAll("<", "\\u003c");
const html = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><main id="fixture-root" style="max-width:1100px;margin:20px auto;padding:14px"></main><script type="module">
import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type; window.__vite_plugin_react_preamble_installed__ = true;
await import('/src/styles.css');
const {default:React} = await import('/node_modules/.vite/deps/react.js'); const {default:ReactDOM} = await import('/node_modules/.vite/deps/react-dom_client.js'); const {createRoot}=ReactDOM;
const {LabelAttachment, CompactEvaluationDetail, CompactReferenceComparison, EvaluationSummary} = await import('/src/ReferenceLabels.jsx'); const {default:AnalysisReport} = await import('/src/AnalysisReport.jsx'); const {TestRunDetail} = await import('/src/TestRuns.jsx');
const h=React.createElement; const fixture=${fixture};
function App(){ const [mode,setMode]=React.useState('preview'); const [scope,setScope]=React.useState('fixture-source'); const [attached,setAttached]=React.useState(0); const [runOpen,setRunOpen]=React.useState(false); return h('div',{className:'page-stack'},h('h1',{},'답안·보고서 검증'),h('button',{type:'button',id:'change-scope',onClick:()=>setScope('other-exact-source')},'범위 변경'),h('button',{type:'button',onClick:()=>setRunOpen(true)},'테스트 실행 검증'),h('div',{id:'attachment-result'},attached),h(LabelAttachment,{sourceSystem:scope,onAttached:()=>setAttached(n=>n+1)}),h(CompactReferenceComparison,{evaluation:{...fixture.detail.evaluation,outcome:'failed'}}),h(CompactEvaluationDetail,{detail:fixture.detail,history:[{...fixture.reference,id:'first-reference',revision:1},fixture.reference]}),h(EvaluationSummary,{summary:fixture.summary}),h(AnalysisReport,{detail:fixture.detail,mode,onModeChange:setMode}),runOpen&&h(TestRunDetail,{id:'fixture-run',onBack:()=>setRunOpen(false),onOpen:()=>{}}));}
createRoot(document.getElementById('fixture-root')).render(h(App));
</script></body></html>`;

const passed = [];
async function scenario(name, check, { mobile = false, invalid = false } = {}) {
  const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, serviceWorkers: "block" });
  const calls = [], violations = [], errors = [];
  await context.route("**/*", async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    const json = value => route.fulfill({ contentType: "application/json", body: JSON.stringify(value) });
    if (url.origin !== origin) { violations.push("external"); return route.abort(); }
    if (path === "/auxiliary-ux-smoke") return route.fulfill({ contentType: "text/html", body: html });
    if (!path.startsWith("/api/")) return route.continue();
    calls.push({ path, method: request.method(), body: request.postData() });
    if (request.method() === "POST" && path === "/api/v1/evaluation-labels/preview") return json({ source_system: "fixture-source", source_kind: "synthetic_expected", source_ref: "보안팀_검토1", ai_visible: false, total_rows: 1, matched_count: 1, change_count: 1, unchanged_count: 0, can_confirm: !invalid, preview_token: "fixture-preview", expires_at: new Date(Date.now() + 600000).toISOString(), rows: [{ row_number: 1, event_id: "hidden-event-uuid", analysis_id: "hidden-analysis-uuid", current_label: "false_positive", proposed_label: "true_positive", current_revision: 2, change: true }], errors: invalid ? [{ row_number: 1, field: "event_id", code: "analysis_not_found_in_source" }] : [] });
    if (request.method() === "POST" && path === "/api/v1/evaluation-labels/confirm") { await new Promise(resolve => setTimeout(resolve, 120)); return json({ applied_count: 1, unchanged_count: 0, duplicate: false }); }
    if (request.method() === "GET" && path === "/api/v1/test-runs/fixture-run") return json(testRun);
    if (request.method() === "GET" && path === "/api/v1/test-runs") return json({ items: [], total: 0, limit: 10, offset: 0 });
    violations.push("unmocked " + path); return route.abort();
  });
  const page = await context.newPage(); page.setDefaultTimeout(10000); page.on("pageerror", error => errors.push(error.message));
  try {
    await page.goto(origin + "/auxiliary-ux-smoke"); await page.getByRole("heading", { name: "답안·보고서 검증" }).waitFor();
    if (mobile) await page.evaluate(() => document.documentElement.dataset.theme = "dark");
    await check({ page, calls });
    assert.deepEqual(errors, []); assert.deepEqual(violations, []);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, "page width");
    await page.screenshot({ path: join(output, name + ".png"), fullPage: true });
    passed.push(name); console.log("PASS " + name);
  } catch (error) { await page.screenshot({ path: join(output, name + "-failure.png"), fullPage: true }); console.log(JSON.stringify({ output, errors, violations, body: (await page.locator("body").innerText()).slice(0, 1600) })); throw error; }
  finally { await context.close(); }
}
const dialog = (page, name) => page.getByRole("dialog", { name, exact: true });
async function prepare(page) {
  await page.getByRole("button", { name: "참고 답안 연결", exact: true }).click(); const form = dialog(page, "참고 답안 연결");
  await form.getByLabel("참고 답안 JSON", { exact: true }).setInputFiles({ name: "answers.json", mimeType: "application/json", buffer: Buffer.from('[{"event_id":"hidden-event-uuid","expected_verdict":"true_positive"}]') });
  await form.getByLabel("답안 출처 / 버전 이름", { exact: true }).fill("보안팀_검토1");
  await form.getByLabel("답안 작성 시 AI 결과 열람", { exact: true }).selectOption("false"); return form;
}
try {
  await scenario("answer-draft-preview-confirm", async ({ page, calls }) => {
    const form = await prepare(page);
    assert.equal(await form.getByLabel("분석 출처", { exact: true }).inputValue(), "fixture-source");
    await form.getByRole("button", { name: "참고 답안 연결 닫기", exact: true }).click();
    await page.locator("#change-scope").click(); await page.getByRole("button", { name: "참고 답안 연결", exact: true }).click();
    assert.equal(await form.getByLabel("분석 출처", { exact: true }).inputValue(), "fixture-source", "scope change must not overwrite draft");
    assert.equal(await form.getByLabel("답안 출처 / 버전 이름", { exact: true }).inputValue(), "보안팀_검토1");
    assert.equal(await form.locator('input[type="file"]').evaluate(node => node.files.length), 1);
    assert.equal(await form.getByRole("button", { name: "현재 범위의 출처 사용" }).count(), 1);
    await form.getByRole("button", { name: "연결 미리보기", exact: true }).click(); const preview = dialog(page, "답안 연결 미리보기"); await preview.waitFor();
    assert.equal(calls.length, 1); assert.match(calls[0].body, /name="source_system"\r\n\r\nfixture-source/); assert.match(calls[0].body, /name="ai_visible"\r\n\r\nfalse/);
    assert.equal(await preview.getByRole("button", { name: "답안 연결 확정", exact: true }).isDisabled(), true);
    assert.equal(await preview.getByRole("button", { name: "미리보기 유효기간 설명" }).count(), 0);
    await preview.getByRole("button", { name: "미리보기 대상 범위 설명" }).focus(); await page.getByRole("tooltip").waitFor();
    assert.match(await page.getByRole("tooltip").innerText(), /fixture-source/); assert.match(await page.getByRole("tooltip").innerText(), /다른 관리자가 답안을 변경했거나 미리보기가 만료/); await page.keyboard.press("Escape");
    assert.doesNotMatch(await preview.innerText(), /hidden-event-uuid|hidden-analysis-uuid/);
    await preview.getByRole("button", { name: "1행 연결 정보" }).click(); const row = dialog(page, "연결 대상 상세");
    assert.match(await row.innerText(), /hidden-event-uuid/); await page.keyboard.press("Escape"); await row.waitFor({ state: "hidden" }); assert.equal(await preview.isVisible(), true);
    await preview.getByRole("checkbox").check();
    await preview.getByRole("button", { name: "답안 연결 확정", exact: true }).evaluate(node => { node.click(); node.click(); });
    await preview.waitFor({ state: "hidden" }); assert.match(await form.innerText(), /답안 연결 완료/);
    assert.equal(calls.filter(call => call.path.endsWith("/confirm")).length, 1); assert.deepEqual(JSON.parse(calls[1].body), { preview_token: "fixture-preview" });
    assert.equal(await page.locator("#attachment-result").innerText(), "1");
  });
  await scenario("answer-errors-mobile", async ({ page, calls }) => {
    const form = await prepare(page); await form.getByRole("button", { name: "연결 미리보기", exact: true }).click();
    const preview = dialog(page, "답안 연결 미리보기"); await preview.waitFor();
    assert.match(await preview.innerText(), /일부 행만 자동 연결하지 않습니다/); assert.equal(await preview.getByRole("button", { name: "답안 연결 확정" }).count(), 0); assert.equal(calls.length, 1);
    const help = preview.getByRole("button", { name: "1행 오류 정보 설명" }); await help.focus();
    await page.getByRole("tooltip").waitFor(); assert.match(await page.getByRole("tooltip").innerText(), /analysis_not_found_in_source/);
    await page.keyboard.press("Escape"); assert.equal(await preview.isVisible(), true); assert.equal(await page.getByRole("tooltip").count(), 0);
  }, { mobile: true, invalid: true });
  await scenario("history-and-exclusions", async ({ page, calls }) => {
    assert.equal(await page.getByRole("button", { name: "답안 비교 설명" }).count(), 0, "ordinary matching answer does not need a repeated help icon");
    assert.equal(await page.getByRole("button", { name: "평가 범위 설명" }).count(), 0, "evaluation notes are combined into one help");
    assert.equal(await page.getByRole("button", { name: "Accuracy 설명", exact: true }).count(), 1, "requested metric help stays");
    await page.getByRole("button", { name: "평가 제외 사유 설명" }).focus(); await page.getByRole("tooltip").waitFor(); assert.match(await page.getByRole("tooltip").innerText(), /실행 실패/); await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "연결 이력", exact: true }).click(); const history = dialog(page, "참고 답안 이력"); await history.waitFor();
    assert.equal(await history.locator("tbody tr").count(), 2); assert.doesNotMatch(await history.innerText(), /hidden-reference-uuid|blocked.invalid/);
    assert.equal(await history.getByRole("button", { name: "답안 이력 설명" }).count(), 0);
    assert.match(await history.innerText(), /이전 답안과 AI 판정은 보존됩니다.*답안 변경은 모델 입력에 반영하지 않습니다/);
    await history.getByRole("button", { name: "답안 버전 2 상세" }).click(); const row = dialog(page, "답안 연결 상세"); assert.match(await row.innerText(), /hidden-reference-uuid/); assert.match(await row.innerText(), /<img src=/); assert.equal(await row.locator("img").count(), 0);
    await page.keyboard.press("Escape"); await row.waitFor({ state: "hidden" }); await page.keyboard.press("Escape"); await history.waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "출처별 지표 · 1개", exact: true }).click(); const sources = dialog(page, "답안 출처별 지표");
    assert.equal(await sources.getByRole("button", { name: "출처별 비교 설명" }).count(), 0);
    assert.match(await sources.innerText(), /답안 출처·AI 열람 여부별 표본.*서로 다른 그룹을 하나의 독립 정확도로 해석하지/);
    assert.equal(await sources.getByRole("button", { name: "기준 판정 일치율 설명", exact: true }).count(), 1);
    assert.equal(await sources.getByRole("button", { name: "확정 판정 비율 설명", exact: true }).count(), 1);
    assert.match(await sources.innerText(), /50.0% \(1 \/ 2건\)/); assert.match(await sources.innerText(), /66.7% \(2 \/ 3건\)/); await page.keyboard.press("Escape"); await sources.waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "제외·보류 내역", exact: true }).click(); const exclusions = dialog(page, "평가 제외·보류 내역");
    assert.match(await exclusions.innerText(), /실행 실패 1/);
    assert.equal(await exclusions.getByRole("button", { name: "보류 기대 문항 설명" }).count(), 0);
    assert.match(await exclusions.innerText(), /참고 답안이 보류인 문항은.*Accuracy·Precision·Recall·F1 계산에서 제외/); assert.equal(calls.length, 0);
  });
  await scenario("report-reading-mobile", async ({ page, calls }) => {
    const report = page.getByRole("region", { name: "보고서 보기 설정", exact: true });
    assert.doesNotMatch(await report.innerText(), /hidden-analysis-uuid|hidden-event-uuid/);
    assert.equal(await report.getByRole("button", { name: "문서 포함 범위 설명" }).count(), 0);
    assert.equal(await report.getByRole("button", { name: "보고서 부록 설명" }).count(), 0);
    assert.equal(await report.locator(".metric-help-trigger").count(), 0);
    assert.match(await report.locator(".report-appendix-option").innerText(), /부록·조회한 디코딩은 화면과 Markdown에만 포함 · PDF·Excel 제외/);
    await report.getByLabel("보고서 목차").selectOption({ label: "판정 근거" });
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("class")), "report-section");
    assert.equal(await report.locator(".report-excerpt code").textContent(), detail.result.evidence[0].excerpt);
    assert.equal(await report.locator("img").count(), 0);
    await report.getByRole("button", { name: "Markdown 원본", exact: true }).click(); const source = report.getByLabel("보고서 Markdown 원본", { exact: true });
    assert.match(await source.innerText(), /상위 디렉터리 접근/);
    await report.getByRole("checkbox", { name: "평가·실행 부록 포함", exact: true }).check(); assert.match(await source.innerText(), /부록 · 참고 답안 평가/);
    await report.getByRole("button", { name: "보고서 보기", exact: true }).click(); assert.match(await report.innerText(), /hidden-analysis-uuid|hidden-event-uuid/); assert.equal(calls.length, 0);
  }, { mobile: true });
  await scenario("test-detail-help-consolidation", async ({ page, calls }) => {
    await page.getByRole("button", { name: "테스트 실행 검증", exact: true }).click();
    const run = page.locator(".test-run-detail"); await run.getByRole("heading", { name: "검토용 테스트", exact: true }).waitFor();
    assert.equal(await run.getByRole("button", { name: "테스트 평가 기준 설명" }).count(), 0);
    assert.equal(await run.getByRole("button", { name: "테스트 비교 설명" }).count(), 0);
    assert.equal(await run.getByRole("button", { name: "테스트 범위 설명" }).count(), 0);
    assert.equal(await run.getByRole("button", { name: "문항 필터 설명" }).count(), 0);
    assert.match(await run.locator(".test-run-scope").innerText(), /접수 당시 난이도·유형으로 지표와 문항 목록을 함께 좁힙니다/);
    assert.match(await run.locator(".test-run-cases").innerText(), /상태·답안 비교·행렬 선택은 문항 목록만 좁힙니다.*위 지표는 유지됩니다/);
    await run.getByRole("button", { name: "실행 정보", exact: true }).click(); const metadata = dialog(page, "테스트 실행 정보"); await metadata.waitFor();
    assert.match(await metadata.innerText(), /접수 당시 답안·모델·지침/); assert.match(await metadata.innerText(), /사후 답안 연결과 실패 재실행은 이 테스트의 기존 지표를 바꾸지 않습니다/); assert.match(await metadata.innerText(), /fixture-source/);
    await page.keyboard.press("Escape"); await metadata.waitFor({ state: "hidden" });
    await run.getByRole("button", { name: "다른 테스트와 비교", exact: true }).click(); const comparison = dialog(page, "테스트 비교"); await comparison.waitFor();
    assert.equal(await comparison.getByRole("button", { name: "비교 실행 설명" }).count(), 0);
    assert.match(await comparison.innerText(), /같은 입력·답안의 저장된 결과만 비교하며 모델을 호출하지 않습니다/);
    assert.match(await comparison.innerText(), /모델·지침 변경만의 효과를 입증하는 실험은 아닙니다/);
    assert.ok(calls.every(call => call.method === "GET" && call.path.startsWith("/api/v1/test-runs")));
  });
  console.log(JSON.stringify({ passed, output, actualApiCalls: 0, llmCalls: 0 }));
} finally { await browser.close(); }
