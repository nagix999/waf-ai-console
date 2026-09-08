/**
 * Settings UX browser regression. All API calls are intercepted fixtures;
 * unexpected writes and external traffic fail closed. No server/LLM is called.
 * Start Vite on 127.0.0.1:15173 separately. Reuses installed Playwright/Chromium.
 * NAVIGATION_PLAYWRIGHT_MODULE and NAVIGATION_CHROMIUM override local paths.
 */
import assert from "node:assert/strict";
import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const origin = "http://127.0.0.1:15173";
const now = "2026-09-08T00:00:00Z";
const id = n => `${String(n).padStart(8, "0")}-1111-4111-8111-111111111111`;
const hash = "b".repeat(64);
const fields = [
  { name: "event_id", description: "이벤트 식별자", type: "string", required: true, nullable: false, min_length: 1, max_length: 255 },
  { name: "payload", description: "HTTP 요청 원문", type: "string", required: true, nullable: false, min_length: 1, max_length: 2000000 },
  { name: "waf_action", description: "차단 여부", type: "string", required: true, nullable: false, enum: ["D", "A"] },
];
const schemas = [1, 2].map(n => ({ id: id(n), version_number: n, name: `예시 스키마 ${n}`, change_note: "브라우저 점검용 정의입니다.", content_hash: hash, created_at: now, created_by: "fixture-admin", parent_id: n === 2 ? id(1) : null, fields }));
const policies = [3, 4].map(n => ({ id: id(n), version_number: n - 2, name: `예시 지침 ${n - 2}`, change_note: "브라우저 점검용 지침입니다.", content_hash: hash, created_at: now, created_by: "fixture-admin", parent_version_id: n === 4 ? id(3) : null, policy_text: n === 3 ? "관측한 근거를 분리해서 작성하세요." : "확인한 공격 구문과 근거를 분리해서 작성하세요." }));
const profiles = [
  { id: id(5), name: "예시 운영 모델", provider: "vllm", base_url: "http://10.1.2.3:8000/v1", model_name: "fixture-model", status: "production", is_test: false, external_data_approved: false },
  { id: id(6), name: "예시 외부 모델", provider: "openai", base_url: "https://api.openai.com/v1", model_name: "fixture-openai", status: "verified", is_test: true, external_data_approved: true },
].map(p => ({ ...p, context_window: 32768, max_output_tokens: 4096, timeout_seconds: 120, test_concurrency: 1, tls_verify: true, has_api_key: true, can_assign: true, profile_fingerprint: "a".repeat(64), created_at: now }));
const targets = [
  { id: id(7), ip_address: "10.1.2.3", port: 8000, description: "예시 운영 서버", revision: 1, in_use_profiles: [profiles[0]] },
  { id: id(8), ip_address: "10.1.2.4", port: 8000, description: "예시 대기 서버", revision: 1, in_use_profiles: [] },
];
const originalKey = { id: id(9), name: "예시 수집기 키", source_system: "fixture-collector", key_prefix: "fixture-prefix", scopes: ["ingest", "review"], created_at: now, last_used_at: null, revoked_at: null };
const datasetSummary = { total: 150, labeled: 150, evaluable: 150, matches: 150, binary_evaluable: 150, binary_decided: 150, support_positive: 100, support_negative: 50, label_coverage: 1, outcomes: { match: 150 }, source_groups: [], confusion_matrix: { tp: 100, tn: 50, fp: 0, fn: 0, abstained_positive: 0, abstained_negative: 0 }, metrics: { accuracy: 1, precision: 1, recall: 1, f1: 1, coverage: 1, abstention_rate: 0 } };
const modelTest = { id: id(12), name: "예시 완료 검증", profile_id: profiles[1].id, profile_fingerprint: profiles[1].profile_fingerprint, mode: "full", status: "completed", completed_at: now, checks: [], include_dataset: true, test_run_id: id(11), dataset_evaluation: { dataset_version: "fixture-dataset", source_system: "waf-internal-model-test-fixture", status: "completed", total: 150, pending: 0, processing: 0, completed: 150, failed: 0, summary: datasetSummary } };
const markdown = `# 예시 운영 API\n\n접수 및 응답 계약을 확인하세요.\n\n적용 입력 스키마: **v1** · \`${id(1)}\`\n정의 SHA-256: \`${hash}\`\n\n## 인증\n\nX-API-Key 인증입니다.\n\n## 요청 입력\n\n예시 요청을 확인하세요.\n\n\`\`\`json\n{"event_id":"fixture-event","payload":"GET / HTTP/1.1"}\n\`\`\`\n\n## 응답 결과\n\n분석 접수 번호를 반환합니다.\n`;

async function existingPlaywright() {
  if (process.env.NAVIGATION_PLAYWRIGHT_MODULE) return import(pathToFileURL(process.env.NAVIGATION_PLAYWRIGHT_MODULE).href);
  try { return await import("playwright"); } catch { /* Existing Python package has a JS driver. */ }
  const archive = join(homedir(), ".cache/uv/archive-v0");
  for (const entry of existsSync(archive) ? readdirSync(archive).sort() : []) {
    const candidate = join(archive, entry, "playwright/driver/package/index.mjs");
    if (existsSync(candidate)) return import(pathToFileURL(candidate).href);
  }
  throw new Error("An existing Playwright installation is required.");
}

const { chromium } = await existingPlaywright();
const executablePath = process.env.NAVIGATION_CHROMIUM || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome");
assert.ok(existsSync(executablePath), "An existing Chromium executable is required.");
const browser = await chromium.launch({ executablePath, headless: true, args: ["--no-sandbox"] });
const passed = [], screenshots = [];
let requestsTotal = 0;

async function scenario(name, check, { mobile = false, dark = false } = {}) {
  const context = await browser.newContext({ viewport: { width: mobile ? 390 : 1280, height: mobile ? 844 : 1000 }, colorScheme: dark ? "dark" : "light", serviceWorkers: "block" });
  const violations = [], pageErrors = [], requests = [];
  let keys = [{ ...originalKey }], releaseDelete, releaseQuick;
  const quickRequests = [];
  const deleteGate = new Promise(resolve => { releaseDelete = resolve; });
  const quickGate = new Promise(resolve => { releaseQuick = resolve; });
  await context.route("**/*", async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method(), path = url.pathname;
    const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (url.origin !== origin) { violations.push("external_request"); return route.abort("blockedbyclient"); }
    if (!path.startsWith("/api/")) {
      if (method === "GET" && (path === "/" || /^\/(src|node_modules|assets|@vite|@react-refresh|@fs)\b/.test(path) || path === "/favicon.ico")) return route.continue();
      violations.push("unexpected_non_api_request"); return route.abort("blockedbyclient");
    }
    requests.push({ path, method }); requestsTotal++;
    if (method === "DELETE" && path === `/api/v1/admin/service-api-keys/${originalKey.id}`) {
      await deleteGate; keys = keys.filter(key => key.id !== originalKey.id); return route.fulfill({ status: 204 });
    }
    if (method === "POST" && path === "/api/v1/admin/service-api-keys") {
      const payload = request.postDataJSON();
      const item = { ...originalKey, ...payload, id: id(10), key_prefix: "fixture-issued" };
      keys.push(item); return json({ item, api_key: "FIXTURE_ONLY_ONE_TIME_VALUE" }, 201);
    }
    if (method === "POST" && /^\/api\/v1\/admin\/input-schemas\/[^/]+\/validate$/.test(path)) return json({ valid: true, validation_token: "fixture-validation", expires_in_seconds: 300, issues: [] });
    if (method === "POST" && path === `/api/v1/model-profiles/${profiles[1].id}/tests`) {
      const payload = request.postDataJSON(); quickRequests.push(payload);
      if (payload.mode !== "quick" || payload.include_dataset !== false || payload.expected_profile_fingerprint !== profiles[1].profile_fingerprint) {
        violations.push("invalid_quick_fixture_contract"); return json({ detail: "invalid_fixture_contract" }, 422);
      }
      await quickGate;
      return json({ ...modelTest, id: id(13), name: payload.name, mode: "quick", include_dataset: false, dataset_evaluation: null, test_run_id: null }, 202);
    }
    if (method !== "GET") { violations.push(`unexpected_api_write:${method}:${path}`); return json({ detail: "blocked_fixture_write" }, 403); }
    if (path === "/api/v1/auth/me") return json({ kind: "admin", id: "fixture-admin", username: "fixture-admin", role: "admin", scopes: ["admin"] });
    if (path === "/api/v1/dashboard/summary") return json({ counts: { total: 0, pending: 0, processing: 0, completed: 0, failed: 0, true_positive: 0, false_positive: 0, inconclusive: 0, critical_high_allowed: 0, false_positive_denied: 0 }, runtime: { agent_mode: "moduagent", production_profile: profiles[0] }, window: { created_from: now, created_to: now } });
    if (path === "/api/v1/model-profiles") return json(profiles);
    if (/^\/api\/v1\/model-profiles\/[^/]+\/tests$/.test(path)) return json(path.includes(profiles[1].id) ? [modelTest] : []);
    if (path === "/api/v1/admin/internal-egress") return json(targets);
    if (path === "/api/v1/admin/service-api-keys") return json({ items: keys });
    if (path === "/api/v1/admin/prompt-policies") return json({ items: policies, active_version_id: policies[0].id, revision: 1, max_policy_chars: 4000, fixed_instructions: "브라우저 확인용 읽기 전용 규칙입니다.", fixed_rules_version: "fixture-v1" });
    if (path.startsWith("/api/v1/admin/prompt-policies/")) { const policy = policies.find(p => path.endsWith(p.id)); if (policy) return json(policy); }
    if (path === "/api/v1/admin/input-schemas") return json({ items: schemas, active_version_id: schemas[0].id, revision: 1, default_fields: fields });
    if (path === "/api/v1/admin/input-schemas/activation-history") return json({ items: [] });
    if (path.startsWith("/api/v1/admin/input-schemas/")) { const schema = schemas.find(p => path.endsWith(p.id)); if (schema) return json(schema); }
    if (path === "/api/v1/production-api") return json({ markdown, input_schema: { version_id: id(1), version_number: 1, content_hash: hash } });
    violations.push(`unmocked_api_read:${path}`); return json({ detail: "unmocked_fixture_endpoint" }, 500);
  });
  const page = await context.newPage(); page.setDefaultTimeout(8000);
  page.on("pageerror", error => pageErrors.push(error.name));
  page.on("dialog", async native => { violations.push(`unexpected_native_dialog:${native.type()}`); await native.dismiss(); });
  const go = async hashRoute => {
    await page.goto(`${origin}/${hashRoute}`); await page.locator(".app-header h1").waitFor();
    if (dark && await page.getByRole("button", { name: "다크 모드로 전환", exact: true }).count()) await page.getByRole("button", { name: "다크 모드로 전환", exact: true }).click();
    if (dark) assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
    const heading = await page.locator(".app-header").evaluate(header => {
      const title = header.querySelector("h1"), description = header.querySelector(".page-description");
      return { title: title?.textContent?.trim(), description: description?.textContent?.trim(), helpCount: header.querySelectorAll(".metric-help-trigger").length,
        titleColor: title && getComputedStyle(title).color, descriptionColor: description && getComputedStyle(description).color,
        titleSize: title && Number.parseFloat(getComputedStyle(title).fontSize), descriptionSize: description && Number.parseFloat(getComputedStyle(description).fontSize),
        titleBottom: title?.getBoundingClientRect().bottom, descriptionTop: description?.getBoundingClientRect().top };
    });
    assert.ok(heading.title && heading.description, "Page title and short inline description must be visible.");
    assert.equal(heading.helpCount, 0, "Page heading must not repeat its description behind a help button.");
    assert.notEqual(heading.descriptionColor, heading.titleColor, "Description must use the muted color.");
    assert.ok(heading.descriptionSize < heading.titleSize && heading.descriptionTop >= heading.titleBottom, "Description must sit below the title with smaller text.");
  };
  const dialog = title => page.getByRole("dialog", { name: title, exact: true });
  const close = async title => { await dialog(title).getByRole("button", { name: `${title} 닫기`, exact: true }).click(); await dialog(title).waitFor({ state: "hidden" }); };
  const shot = async suffix => { const path = `/tmp/waf-settings-${name}-${suffix}.png`; await page.screenshot({ path, fullPage: false }); screenshots.push(path); };
  const noOverflow = async () => {
    const result = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth, dialogs: [...document.querySelectorAll("dialog[open]")].map(node => { const rect = node.getBoundingClientRect(); return { left: rect.left, right: rect.right, width: rect.width, client: node.clientWidth, scroll: node.scrollWidth }; }) }));
    assert.ok(result.document <= result.width + 1, `Page horizontal overflow: ${JSON.stringify(result)}`);
    for (const box of result.dialogs) assert.ok(box.left >= 0 && box.right <= result.width + 1 && box.scroll <= box.client + 1, `Dialog horizontal overflow: ${JSON.stringify(box)}`);
  };
  const tooltip = async (container, label) => {
    const trigger = container.getByRole("button", { name: `${label} 설명`, exact: true });
    await trigger.focus(); const help = page.getByRole("tooltip"); await help.waitFor();
    const info = await help.evaluate(node => { const rect = node.getBoundingClientRect(), top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2); return { onTop: top === node || node.contains(top), insideDialog: Boolean(node.closest("dialog[open]")), left: rect.left, right: rect.right, width: innerWidth, background: getComputedStyle(node).backgroundColor }; });
    assert.ok(info.onTop && info.insideDialog, `Modal tooltip must be in the top layer: ${JSON.stringify(info)}`);
    assert.ok(info.left >= 0 && info.right <= info.width + 1, "Tooltip must fit the viewport.");
    if (dark) assert.notEqual(info.background, "rgb(255, 255, 255)", "Dark mode tooltip must not use a white fallback.");
    await page.keyboard.press("Escape"); await help.waitFor({ state: "hidden" });
    assert.ok(await container.isVisible(), "Escape on tooltip must not dismiss its containing dialog.");
  };
  try {
    await check({ page, go, dialog, close, shot, noOverflow, tooltip, requests, releaseDelete, quickRequests, releaseQuick });
    assert.deepEqual(violations, [], `${name}: every API call must remain mocked`);
    assert.deepEqual(pageErrors, [], `${name}: no browser runtime error`);
    passed.push(name); console.log(`PASS ${name}`);
  } catch (error) {
    console.log(JSON.stringify({ scenario: name, diagnostic: await page.evaluate(() => ({ dialogs: [...document.querySelectorAll("dialog")].map(node => ({ title: node.querySelector("h2")?.textContent, open: node.open, modal: node.matches(":modal"), inert: node.inert, display: getComputedStyle(node).display, position: getComputedStyle(node).position })), active: document.activeElement?.tagName, apiErrors: [...document.querySelectorAll('[role="alert"]')].length })), aria: await page.locator("dialog[open]").first().ariaSnapshot().catch(() => "no_dialog") }));
    await shot("failure"); throw error;
  }
  finally { releaseDelete(); releaseQuick(); await context.close(); }
}

try {
  await scenario("prompt-draft-and-confirmations", async ({ page, go, dialog, close, noOverflow, tooltip, shot, requests }) => {
    await go("#settings/prompts");
    await page.getByRole("button", { name: "새 버전 작성", exact: true }).waitFor();
    assert.ok(!(await page.locator("body").innerText()).includes(policies[0].id));
    await page.getByRole("button", { name: "기술정보", exact: true }).click();
    assert.ok((await dialog("프롬프트 기술정보").innerText()).includes(hash)); await close("프롬프트 기술정보");
    await page.getByRole("button", { name: "변경 비교", exact: true }).click(); await close("프롬프트 변경 비교");
    await page.getByRole("button", { name: "시스템 규칙 보기", exact: true }).click(); await close("시스템 규칙 · 읽기 전용");
    await page.getByRole("button", { name: /예시 지침 2/ }).click();
    await page.getByRole("button", { name: "공통 적용", exact: true }).click();
    const confirm = dialog("공통 프롬프트 적용");
    assert.match(await confirm.innerText(), /Production과 Test.*함께/);
    assert.ok(await confirm.getByRole("button", { name: "공통 적용 확인", exact: true }).isDisabled());
    await confirm.getByRole("checkbox").check();
    assert.ok(await confirm.getByRole("button", { name: "공통 적용 확인", exact: true }).isEnabled());
    await confirm.getByRole("button", { name: "취소", exact: true }).click();
    await page.getByRole("button", { name: "새 버전 작성", exact: true }).click();
    const draft = dialog("새 프롬프트 버전 작성");
    await draft.getByLabel("버전 이름", { exact: false }).fill("보존할 프롬프트 초안");
    assert.match(await draft.innerText(), /실제 원문·Cookie·API 키/);
    assert.match(await draft.innerText(), /긴 지침은 로그 입력 공간을 줄입니다.*컨텍스트가 부족하면 모델을 호출할 수 없습니다/s);
    assert.equal(await draft.getByRole("button", { name: "지침 길이와 모델 입력 설명", exact: true }).count(), 0);
    await noOverflow(); await shot("draft");
    await draft.getByRole("button", { name: "복제 원본과 초안 비교", exact: true }).click();
    await dialog("프롬프트 초안 비교").waitFor(); await noOverflow(); await close("프롬프트 초안 비교");
    assert.ok(await draft.isVisible());
    await close("새 프롬프트 버전 작성");
    await page.getByRole("button", { name: "작성 중인 버전 열기", exact: true }).click();
    assert.equal(await draft.getByLabel("버전 이름", { exact: false }).inputValue(), "보존할 프롬프트 초안");
    await draft.getByRole("button", { name: "초안 삭제", exact: true }).click();
    const discard = dialog("프롬프트 초안 삭제"); assert.match(await discard.innerText(), /복구할 수 없으며.*공통 적용 지침은 바뀌지/);
    await noOverflow(); await discard.getByRole("button", { name: "계속 작성", exact: true }).click();
    assert.equal(await draft.getByLabel("버전 이름", { exact: false }).inputValue(), "보존할 프롬프트 초안");
    await draft.getByRole("button", { name: "초안 삭제", exact: true }).click();
    await discard.getByRole("button", { name: "초안 삭제 확인", exact: true }).click(); await draft.waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "새 버전 작성", exact: true }).click();
    assert.notEqual(await draft.getByLabel("버전 이름", { exact: false }).inputValue(), "보존할 프롬프트 초안"); await close("새 프롬프트 버전 작성");
    assert.ok(requests.every(r => r.method === "GET"));
  });

  await scenario("schema-empty-sample-draft-mobile-dark", async ({ page, go, dialog, close, tooltip, noOverflow, shot, requests }) => {
    await go("#settings/schema");
    await page.getByRole("button", { name: /v2 · 예시 스키마 2/ }).click();
    await page.getByRole("button", { name: "샘플 검증 · 적용", exact: true }).click();
    const validate = dialog("샘플 검증 · 적용"), sample = validate.getByRole("textbox", { name: "검증할 샘플", exact: true });
    assert.equal(await sample.inputValue(), ""); assert.ok(await sample.getAttribute("placeholder"));
    assert.ok(await validate.getByRole("button", { name: "샘플 검증", exact: true }).isDisabled());
    assert.ok(await validate.getByRole("button", { name: "검증한 버전 운영 적용", exact: true }).isDisabled());
    assert.match(await validate.innerText(), /기존 수집기의 새 요청이 거절/);
    await tooltip(validate, "샘플 검증");
    await sample.fill('{"event_id":"fixture-event","payload":"GET / HTTP/1.1","waf_action":"D"}');
    await validate.getByRole("button", { name: "샘플 검증", exact: true }).click();
    await validate.getByText(/샘플이 접수 기준을 통과했습니다/).waitFor();
    await validate.getByRole("checkbox").check();
    assert.ok(await validate.getByRole("button", { name: "검증한 버전 운영 적용", exact: true }).isEnabled());
    await noOverflow(); await shot("sample"); await close("샘플 검증 · 적용");
    await page.getByRole("button", { name: "샘플 검증 · 적용", exact: true }).click();
    assert.match(await sample.inputValue(), /fixture-event/); await close("샘플 검증 · 적용");
    for (const [button, title] of [["기술정보", "스키마 기술정보"], ["변경 비교", "현재 적용 버전과 비교"], ["적용 이력", "스키마 적용 이력"]]) { await page.getByRole("button", { name: button, exact: true }).click(); await noOverflow(); await close(title); }
    await page.getByRole("button", { name: "새 버전 작성", exact: true }).click();
    const draft = dialog("새 스키마 버전 작성"); await draft.getByLabel("버전 이름", { exact: true }).fill("보존할 스키마 초안");
    assert.match(await draft.innerText(), /공개 API 문서.*비밀값·실제 로그·개인정보/);
    await tooltip(draft, "스키마 편집 범위"); await noOverflow(); await shot("draft");
    await draft.getByRole("button", { name: "event_id 필드 편집", exact: true }).click();
    const field = dialog("필드 편집"); await field.waitFor();
    assert.equal(await field.getByRole("textbox", { name: "필드명", exact: true }).getAttribute("readonly"), "");
    assert.ok(await field.getByRole("combobox", { name: "형식", exact: true }).isDisabled());
    await field.getByRole("textbox", { name: "설명", exact: true }).fill("보존할 필드 설명");
    await tooltip(field, "null 허용"); await noOverflow(); await close("필드 편집");
    await draft.getByRole("button", { name: "복제 원본과 초안 변경 비교", exact: true }).click();
    const comparison = dialog("스키마 초안 변경 비교");
    await comparison.getByRole("button", { name: /event_id.*변경 전후 보기/ }).click();
    assert.match(await dialog("필드 변경 전후").innerText(), /보존할 필드 설명/);
    await noOverflow(); await close("필드 변경 전후"); await close("스키마 초안 변경 비교");
    await draft.getByRole("button", { name: "event_id 필드 편집", exact: true }).click();
    assert.equal(await field.getByRole("textbox", { name: "설명", exact: true }).inputValue(), "보존할 필드 설명"); await close("필드 편집");
    await close("새 스키마 버전 작성"); await page.getByRole("button", { name: "작성 중인 버전 열기", exact: true }).click();
    assert.equal(await draft.getByLabel("버전 이름", { exact: true }).inputValue(), "보존할 스키마 초안");
    await draft.getByRole("button", { name: "초안 삭제", exact: true }).click();
    const discard = dialog("스키마 초안 삭제"); assert.match(await discard.innerText(), /복구할 수 없으며.*현재 접수 기준은 바뀌지/);
    await noOverflow(); await discard.getByRole("button", { name: "계속 작성", exact: true }).click();
    assert.equal(await draft.getByLabel("버전 이름", { exact: true }).inputValue(), "보존할 스키마 초안");
    await draft.getByRole("button", { name: "초안 삭제", exact: true }).click();
    await discard.getByRole("button", { name: "초안 삭제 확인", exact: true }).click(); await draft.waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "새 버전 작성", exact: true }).click();
    assert.notEqual(await draft.getByLabel("버전 이름", { exact: true }).inputValue(), "보존할 스키마 초안");
    await draft.getByRole("button", { name: "event_id 필드 편집", exact: true }).click();
    assert.equal(await field.getByRole("textbox", { name: "설명", exact: true }).inputValue(), fields[0].description); await close("필드 편집"); await close("새 스키마 버전 작성");
    assert.equal(requests.filter(r => r.method !== "GET").length, 1);
  }, { mobile: true, dark: true });

  await scenario("egress-draft-in-use-delete-warning", async ({ page, go, dialog, close, tooltip, noOverflow, requests, shot }) => {
    await go("#settings/egress");
    await page.getByRole("button", { name: "10.1.2.3:8000 편집", exact: true }).click();
    const edit = dialog("내부 대상 편집");
    assert.equal(await edit.getByRole("textbox", { name: /^내부 IP/ }).getAttribute("readonly"), "");
    assert.match(await edit.innerText(), /사용 중인 대상은 설명만 수정/);
    await edit.getByLabel("설명 · 선택", { exact: true }).fill("보존할 서버 설명");
    await close("내부 대상 편집"); await page.getByRole("button", { name: "편집 계속", exact: true }).click();
    assert.equal(await edit.getByLabel("설명 · 선택", { exact: true }).inputValue(), "보존할 서버 설명");
    await tooltip(edit, "내부 IP"); await noOverflow(); await shot("edit");
    await page.keyboard.press("Escape"); await edit.waitFor({ state: "hidden" });
    assert.ok(await page.getByRole("button", { name: "10.1.2.3:8000 삭제", exact: true }).isDisabled());
    await page.getByRole("button", { name: "10.1.2.4:8000 삭제", exact: true }).click();
    assert.match(await dialog("내부 허용 대상 삭제").innerText(), /기존 분석 결과는 보존/);
    await dialog("내부 허용 대상 삭제").getByRole("button", { name: "취소", exact: true }).click();
    await page.getByRole("button", { name: "기술정보", exact: true }).first().click(); await close("내부 대상 기술정보");
    await page.getByRole("button", { name: "편집 계속", exact: true }).click();
    await edit.getByRole("button", { name: "편집 취소", exact: true }).click();
    const discard = dialog("내부 대상 편집 취소"); assert.match(await discard.innerText(), /등록된 내부 IP·포트와 설명은 바뀌지/);
    await noOverflow(); await discard.getByRole("button", { name: "계속 편집", exact: true }).click();
    assert.equal(await edit.getByLabel("설명 · 선택", { exact: true }).inputValue(), "보존할 서버 설명");
    await edit.getByRole("button", { name: "편집 취소", exact: true }).click();
    await discard.getByRole("button", { name: "편집 취소 확인", exact: true }).click(); await edit.waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "10.1.2.3:8000 편집", exact: true }).click();
    assert.equal(await edit.getByLabel("설명 · 선택", { exact: true }).inputValue(), targets[0].description); await close("내부 대상 편집");
    assert.ok(requests.every(r => r.method === "GET"));
    await page.reload(); await page.getByRole("button", { name: "내부 대상 추가", exact: true }).click();
    const create = dialog("내부 대상 추가"); assert.equal(await create.getByRole("textbox", { name: /^내부 IP/ }).inputValue(), "");
    await create.getByRole("textbox", { name: /^내부 IP/ }).fill("10.8.9.10"); await close("내부 대상 추가");
    await page.getByRole("button", { name: "내부 대상 추가", exact: true }).click(); assert.equal(await create.getByRole("textbox", { name: /^내부 IP/ }).inputValue(), "10.8.9.10");
    await close("내부 대상 추가");
  }, { mobile: true, dark: true });

  await scenario("keys-issue-once-delete-once", async ({ page, go, dialog, close, tooltip, noOverflow, requests, releaseDelete, shot }) => {
    await go("#settings/keys");
    await page.getByRole("button", { name: "예시 수집기 키 삭제", exact: true }).waitFor();
    assert.ok(!(await page.locator("body").innerText()).includes(originalKey.id));
    await page.getByRole("button", { name: "기술정보", exact: true }).click(); await close("API 키 기술정보");
    await page.getByRole("button", { name: "예시 수집기 키 이름 변경", exact: true }).click();
    await dialog("키 이름 변경").getByLabel("새 키 이름", { exact: true }).fill("보존할 키 이름"); await close("키 이름 변경");
    await page.getByRole("button", { name: "예시 수집기 키 이름 변경", exact: true }).click();
    assert.equal(await dialog("키 이름 변경").getByLabel("새 키 이름", { exact: true }).inputValue(), "보존할 키 이름"); await close("키 이름 변경");
    await page.getByRole("button", { name: "키 발급", exact: true }).click();
    const create = dialog("새 API 키 발급");
    assert.match(await create.innerText(), /만료일이 없습니다.*한 번만/);
    await create.getByLabel("키 이름", { exact: true }).fill("예시 신규 키");
    await create.getByRole("textbox", { name: /^연동 시스템/ }).fill("fixture-new");
    await create.getByRole("checkbox", { name: "분석 접수·조회", exact: true }).check();
    await tooltip(create, "연동 시스템"); await noOverflow(); await shot("create");
    await close("새 API 키 발급"); await page.getByRole("button", { name: "키 발급", exact: true }).click();
    assert.equal(await create.getByLabel("키 이름", { exact: true }).inputValue(), "예시 신규 키");
    await create.getByRole("button", { name: "API 키 발급", exact: true }).click();
    const issued = dialog("API 키 발급 완료"); await issued.waitFor();
    assert.equal(await issued.getByLabel("발급된 API Key 원문", { exact: true }).inputValue(), "FIXTURE_ONLY_ONE_TIME_VALUE");
    await close("API 키 발급 완료");
    assert.equal(await page.locator("#issued-service-api-key").count(), 0, "Closed one-time value must be removed from the DOM.");
    await page.getByRole("button", { name: "예시 수집기 키 삭제", exact: true }).click();
    const remove = dialog("API 키 삭제");
    assert.match(await remove.innerText(), /목록과 키별 대시보드.*분석 결과와 감사 이력은 보존/s);
    await remove.getByRole("button", { name: "취소", exact: true }).click();
    assert.equal(requests.filter(r => r.method === "DELETE").length, 0);
    await page.getByRole("button", { name: "예시 수집기 키 삭제", exact: true }).click();
    const confirm = remove.getByRole("button", { name: "API 키 삭제", exact: true });
    await confirm.evaluate(button => { button.click(); button.click(); });
    await page.waitForFunction(() => [...document.querySelectorAll("dialog[open] button")].some(button => button.textContent === "삭제 중…" && button.disabled));
    assert.equal(requests.filter(r => r.method === "DELETE").length, 1, "Repeated click must submit a single delete.");
    await noOverflow(); await shot("deleting"); releaseDelete();
    await remove.waitFor({ state: "hidden" });
    assert.equal(await page.getByRole("button", { name: "예시 수집기 키 삭제", exact: true }).count(), 0);
    assert.equal(requests.filter(r => r.method === "POST").length, 1);
    assert.equal(requests.filter(r => r.method === "DELETE").length, 1);
  }, { mobile: true, dark: true });

  await scenario("api-section-selection-mobile-dark", async ({ page, go, dialog, close, noOverflow, shot, requests }) => {
    await go("#production-api"); await page.getByRole("navigation", { name: "API 정의서 목차" }).waitFor();
    assert.equal(await page.locator(".api-doc-section:visible").count(), 1);
    assert.ok(!(await page.locator("body").innerText()).includes(hash));
    await page.getByRole("navigation", { name: "API 정의서 목차" }).getByRole("button", { name: "요청 입력", exact: true }).click();
    assert.equal(await page.locator(".api-doc-section:visible").count(), 1);
    await page.getByLabel("정의서 검색", { exact: true }).fill("응답 결과");
    await page.locator(".api-doc-section").getByRole("heading", { name: "응답 결과", exact: true }).waitFor();
    await noOverflow(); await shot("selected");
    await page.getByRole("button", { name: "기술정보", exact: true }).click();
    assert.match(await dialog("API 정의서 기술정보").innerText(), /입력 스키마 ID/);
    await noOverflow(); await close("API 정의서 기술정보");
    assert.ok(requests.every(r => r.method === "GET"));
  }, { mobile: true, dark: true });

  await scenario("models-nested-dialogs-mobile-dark", async ({ page, go, dialog, close, tooltip, noOverflow, shot, requests }) => {
    await go("#settings/models");
    await page.getByRole("row").filter({ hasText: "예시 외부 모델" }).getByRole("button", { name: "관리", exact: true }).click();
    const management = dialog("예시 외부 모델 · 모델 관리"); await management.waitFor();
    await management.getByRole("button", { name: "참고 답안 비교 집계", exact: true }).click();
    const dataset = dialog("150건 참고 답안 비교 집계"); await dataset.waitFor();
    assert.match(await dataset.innerText(), /전체 150건/); await noOverflow(); await close("150건 참고 답안 비교 집계");
    assert.ok(await management.isVisible());
    await management.getByRole("button", { name: "전체 검증", exact: true }).click();
    const validation = dialog("전체 검증 범위 선택"); await validation.waitFor();
    assert.equal(await validation.getByLabel("테스트명 · 선택", { exact: true }).inputValue(), "");
    assert.ok(await validation.getByRole("button", { name: "연결·기능 검증만", exact: true }).isEnabled());
    assert.match(await validation.innerText(), /두 실행 옵션 모두.*외부 API.*비용/s);
    assert.match(await validation.innerText(), /연결·기본 응답·구조화 출력·시스템 지침·큰 입력·동시 요청.*판정 품질은 보증하지/s);
    await tooltip(validation, "150건 판정 평가"); await noOverflow(); await shot("nested-validation");
    await validation.getByRole("button", { name: "취소", exact: true }).click();
    assert.ok(await management.isVisible());
    await management.getByRole("button", { name: "Production 지정", exact: true }).click();
    const assign = dialog("Production 모델 지정"); await assign.waitFor();
    assert.match(await assign.innerText(), /마스킹하지 않은 payload·Cookie.*외부 API/s);
    await noOverflow(); await assign.getByRole("button", { name: "취소", exact: true }).click();
    assert.ok(await management.isVisible()); await close("예시 외부 모델 · 모델 관리");
    await page.getByRole("button", { name: "모델 추가", exact: true }).click();
    const create = dialog("모델 추가"); await create.getByLabel("프로필 이름", { exact: true }).fill("보존할 모델 초안");
    await tooltip(create, "토큰 한도"); await noOverflow(); await shot("create"); await close("모델 추가");
    await page.getByRole("button", { name: "모델 추가", exact: true }).click();
    assert.equal(await create.getByLabel("프로필 이름", { exact: true }).inputValue(), "보존할 모델 초안");
    await close("모델 추가"); assert.ok(requests.every(r => r.method === "GET"));
  }, { mobile: true, dark: true });
  await scenario("quick-validation-approval-draft-and-dedup", async ({ page, go, dialog, noOverflow, shot, requests, quickRequests, releaseQuick }) => {
    await go("#settings/models");
    await page.getByRole("row").filter({ hasText: "예시 외부 모델" }).getByRole("button", { name: "관리", exact: true }).click();
    const management = dialog("예시 외부 모델 · 모델 관리");
    await management.getByRole("button", { name: "빠른 테스트", exact: true }).click();
    const quick = dialog("빠른 연결 테스트"), name = quick.getByLabel("테스트명 · 선택", { exact: true });
    assert.equal(await name.inputValue(), ""); assert.ok(await name.getAttribute("placeholder"));
    assert.match(await quick.innerText(), /OpenAI로 전송.*API 비용/);
    assert.ok(await quick.getByRole("button", { name: "테스트 시작", exact: true }).isDisabled());
    await name.fill("보존할 빠른 테스트명"); await quick.getByRole("button", { name: "취소", exact: true }).click();
    await quick.waitFor({ state: "hidden" }); assert.ok(await management.isVisible()); assert.equal(quickRequests.length, 0);
    await management.getByRole("button", { name: "빠른 테스트", exact: true }).click();
    assert.equal(await name.inputValue(), "보존할 빠른 테스트명");
    await name.fill(""); await quick.getByRole("checkbox", { name: "외부 전송과 비용을 확인했습니다.", exact: true }).check();
    const submit = quick.getByRole("button", { name: "테스트 시작", exact: true }); assert.ok(await submit.isEnabled());
    await noOverflow(); await shot("approved"); await submit.evaluate(button => { button.click(); button.click(); });
    await page.waitForFunction(() => [...document.querySelectorAll("dialog[open] button")].some(button => button.textContent === "접수 중…" && button.disabled));
    assert.equal(quickRequests.length, 1); assert.equal(quickRequests[0].name, quickRequests[0].idempotency_key);
    assert.match(quickRequests[0].name, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
    assert.equal(quickRequests[0].mode, "quick"); assert.equal(quickRequests[0].include_dataset, false);
    assert.equal(quickRequests[0].expected_profile_fingerprint, profiles[1].profile_fingerprint);
    releaseQuick(); await quick.waitFor({ state: "hidden" });
    assert.equal(requests.filter(request => request.method !== "GET").length, 1);
  }, { mobile: true, dark: true });
  console.log(JSON.stringify({ passed: passed.length, mocked_api_requests: requestsTotal, screenshots }));
} finally { await browser.close(); }
