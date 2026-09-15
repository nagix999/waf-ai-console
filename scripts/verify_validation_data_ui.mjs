// Offline browser smoke test. All API responses are artificial fixtures.
// Build frontend first; pass the installed Playwright driver and Chromium paths.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { resolve, extname } from "node:path";
import { randomUUID } from "node:crypto";

const [driver, executablePath, artifacts] = process.argv.slice(2);
if (!driver || !executablePath || !artifacts) throw new Error("Usage: node scripts/verify_validation_data_ui.mjs DRIVER CHROMIUM ARTIFACT_DIR");
const { chromium } = await import(pathToFileURL(driver));
const root = fileURLToPath(new URL("../frontend/dist/", import.meta.url));
const server = createServer(async (request, response) => {
  const path = resolve(root, "." + new URL(request.url, "http://localhost").pathname);
  if (path !== resolve(root) && !path.startsWith(root)) { response.writeHead(403); response.end(); return; }
  try { const content = await readFile(path === root.slice(0, -1) || path === root ? resolve(root, "index.html") : path);
    response.setHeader("Content-Type", ({ ".js": "application/javascript", ".css": "text/css", ".html": "text/html" })[extname(path)] || "text/html"); response.end(content);
  } catch { response.writeHead(404); response.end(); }
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
let browser;
try {
  browser = await chromium.launch({ executablePath, headless: true, args: ["--no-sandbox"] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(10000);
  const errors = [], mutations = [], datasets = [], keys = [];
  const stamp = "2026-09-15T00:00:00Z", analysisId = randomUUID();
  const evaluation = { outcome: "unlabeled", reference_label: null };
  const summary = { total: 1, labeled: 0, evaluable: 0, matches: 0, binary_evaluable: 0, binary_decided: 0,
    binary_correct: 0, support_positive: 0, support_negative: 0, outcomes: { unlabeled: 1 }, metrics: {}, confusion_matrix: {}, source_groups: [] };
  const analysis = { id: analysisId, status: "completed", event_id: "fixture-event", company_name: "Fixture Company", src_ip: "192.0.2.10", dest_ip: "198.51.100.20", signature: "브라우저 검증 문항", analysis_purpose: "production", ingest_channel: "service_api", verdict: "inconclusive", summary_ko: "테스트용 판정 요약", created_at: stamp, total_elapsed_ms: 1000, evaluation };
  let run;
  page.on("pageerror", error => { errors.push(error.message); console.error("Browser fixture error:", error.message); });
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (!url.pathname.startsWith("/api/")) return route.continue();
    const path = url.pathname, method = route.request().method(), payload = route.request().postDataJSON();
    if (method !== "GET") mutations.push({ path, payload });
    let body, status = 200;
    if (path === "/api/v1/auth/me") body = { kind: "admin_session", scopes: ["admin", "ingest", "review"] };
    else if (path === "/api/v1/dashboard/summary") body = { runtime: { agent_mode: "stub", production_profile: null } };
    else if (path === "/api/v1/model-profiles") body = [];
    else if (path === "/api/v1/admin/service-api-keys") {
      if (method === "POST") { const item = { id: randomUUID(), ...payload, key_prefix: "fixture-public", created_at: stamp }; keys.push(item); body = { item, api_key: "FIXTURE_ONLY_NOT_A_REAL_KEY" }; }
      else body = { items: keys };
    } else if (path === "/api/v1/analyses") body = { items: [analysis], total: 1, evaluation_summary: summary };
    else if (path === "/api/v1/evaluation-labels/selection") body = { items: [{ analysis_id: analysisId, expected_revision: 0, verdict: null }] };
    else if (path === "/api/v1/evaluation-labels/bulk") { analysis.evaluation = { outcome: "expected_abstention_match", reference_label: { verdict: payload.verdict, source_kind: "reference", source_ref: "analyst-reference", ai_visible: true, revision: 1 } }; body = { applied_count: 1, duplicate: false }; }
    else if (path === "/api/v1/validation-datasets") {
      if (method === "POST") { const item = { id: randomUUID(), ...payload, revision: 1, current_revision: 1, version_id: randomUUID(), total: 0, labeled: 0, internal_only: false, items: [], versions: [], created_at: stamp }; datasets.push(item); body = item; status = 201; }
      else body = { items: datasets, total: datasets.length };
    } else if (path.startsWith("/api/v1/validation-datasets/")) {
      const parts = path.split("/"), dataset = datasets.find(item => item.id === parts[4]);
      if (parts[5] === "items" && method === "POST") {
        const item = { id: randomUUID(), item_id: randomUUID(), revision: 1, ...payload, internal_only: false, created_at: stamp };
        dataset.items.push(item); dataset.total++; dataset.labeled += item.reference_verdict ? 1 : 0;
        dataset.revision++; dataset.current_revision = dataset.revision;
        dataset.versions = [{ id: randomUUID(), revision: dataset.revision, created_at: stamp }];
        body = { item, revision: dataset.revision }; status = 201;
      } else if (parts[5] === "imports") body = { added: 1, duplicates: 0, conflicts: [], rejected: [], revision: dataset.revision };
      else if (parts[5] === "runs") {
        run = { id: randomUUID(), name: payload.name || "자동 테스트", kind: "dataset", status: "completed", created_at: stamp,
          accepted: 1, rejected: 0, pending: 0, processing: 0, completed: 1, failed: 0, total: 1, duplicates: 0,
          execution_mode: "stub", profile_metadata: {}, prompt_version: "fixture", source_system: "fixture", total_elapsed_ms: 1000,
          facets: { difficulties: [], test_categories: [] }, items: [], total_items: 0, limit: 25, offset: 0,
          evaluation_summary: summary, accepting_items: false };
        body = run; status = 202;
      } else if (parts[5] === "items") {
        const item = dataset.items.find(item => item.item_id === parts[6]); body = { ...item, history: [item] };
      } else body = dataset;
    } else if (path.endsWith("/evaluations")) body = { items: [] };
    else if (path.startsWith("/api/v1/test-runs/")) body = run;
    else { status = 404; body = { detail: "fixture_route_not_defined" }; }
    return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto(origin + "/#datasets");
  await page.getByRole("button", { name: "데이터셋 만들기" }).click();
  await page.getByLabel("데이터셋명", { exact: true }).fill("브라우저 검증 데이터셋");
  await page.getByRole("dialog").getByRole("button", { name: "저장", exact: true }).click();
  await page.getByRole("button", { name: "문항 추가", exact: true }).click();
  const dialog = page.getByRole("dialog");
  for (const [label, value] of [["문항명", "SQL 문항"], ["이벤트 ID", "fixture-event"], ["회사", "Fixture Company"], ["출발지 IP", "192.0.2.10"], ["목적지 IP", "198.51.100.20"], ["WAF 종류", "fixture"]]) await dialog.getByLabel(label, { exact: true }).fill(value);
  await dialog.getByLabel("WAF 조치").selectOption("D");
  await dialog.getByLabel("HTTP 원문").fill("GET /fixture HTTP/1.1\r\nHost: fixture.invalid\r\n\r\n");
  await dialog.getByLabel("참고 답안", { exact: true }).selectOption("inconclusive");
  await dialog.getByLabel("메모", { exact: true }).fill("브라우저 검증 메모");
  await dialog.getByRole("button", { name: "문항 저장" }).click();
  await page.getByRole("button", { name: "SQL 문항", exact: true }).waitFor();
  await page.screenshot({ path: resolve(artifacts, "dataset-detail.png"), fullPage: true });
  await page.getByRole("button", { name: "← 데이터셋 목록" }).click();
  await page.goBack();
  await page.getByRole("button", { name: "문항 추가", exact: true }).waitFor();
  await page.goto(origin + "/#analyses/production");
  await page.getByLabel("현재 페이지 전체 선택").check();
  await page.getByRole("button", { name: "참고 답안 일괄 입력" }).click();
  await page.getByRole("dialog").getByLabel("참고 답안", { exact: true }).selectOption("inconclusive");
  await page.getByRole("dialog").getByLabel("메모", { exact: true }).fill("일괄 메모 fixture");
  await page.getByRole("dialog").getByRole("button", { name: "답안 저장" }).click();
  await page.getByText("1건의 참고 답안을 저장했습니다.", { exact: false }).waitFor();
  await page.getByLabel("현재 페이지 전체 선택").check();
  await page.getByRole("button", { name: "데이터셋에 추가", exact: true }).click();
  await page.getByRole("dialog").getByLabel("데이터셋", { exact: true }).selectOption(datasets[0].id);
  await page.getByRole("dialog").getByRole("button", { name: "추가", exact: true }).click();
  await page.getByText("1건 추가 · 0건 중복 제외", { exact: false }).waitFor();
  await page.goto(origin + "/#test");
  await page.getByRole("tab", { name: "검증 데이터셋 분석", exact: true }).click();
  const panel = page.getByRole("tabpanel", { name: "검증 데이터셋 분석" });
  await panel.getByLabel("데이터셋", { exact: true }).selectOption(datasets[0].id);
  await panel.getByRole("button", { name: "분석 시작" }).click();
  await page.getByRole("heading", { name: "자동 테스트", exact: true }).waitFor();
  await page.goto(origin + "/#settings/keys");
  await page.getByRole("button", { name: "키 발급", exact: true }).click();
  await page.getByRole("dialog").getByLabel("용도", { exact: true }).selectOption("test");
  await page.getByRole("dialog").getByLabel("키 이름", { exact: true }).fill("브라우저 Test 키");
  await page.getByRole("dialog").getByRole("textbox", { name: "연동 시스템" }).fill("fixture-client");
  await page.getByRole("dialog").getByLabel("분석 접수·조회", { exact: true }).check();
  await page.getByRole("dialog").getByRole("button", { name: "API 키 발급", exact: true }).click();
  await page.getByRole("button", { name: "안전하게 보관했습니다 · 원문 닫기" }).click();
  await page.getByRole("region", { name: "발급된 서비스 API Key 목록" }).getByText("Test · 테스트 분석", { exact: true }).waitFor();
  await page.screenshot({ path: resolve(artifacts, "test-api-key.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(origin + "/#datasets");
  await page.getByRole("button", { name: "브라우저 검증 데이터셋", exact: true }).waitFor();
  await page.screenshot({ path: resolve(artifacts, "datasets-mobile.png"), fullPage: true });
  assert.equal(mutations.filter(call => call.path === "/api/v1/evaluation-labels/bulk").length, 1);
  assert.equal(mutations.find(call => call.path === "/api/v1/admin/service-api-keys").payload.purpose, "test");
  assert.deepEqual(errors, []);
  console.log("Passed: dataset creation, case editing, back navigation, bulk answers, dataset import/run, Test key, mobile layout. API calls are mocked; no model calls.");
} finally { if (browser) await browser.close(); await new Promise(resolve => server.close(resolve)); }
