import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "./api.js";
import { createServiceKeysController, emptyServiceKeysState, serviceKeyError, serviceKeyMetadata, serviceKeyNameError, validateServiceKeyDraft } from "./serviceApiKeys.js";

const item = (extra = {}) => ({ id: "synthetic-key-id", name: "합성 키", key_prefix: "wafsvc_syntheticpublicid", source_system: "synthetic-collector", scopes: ["ingest"], created_at: "2026-09-07T00:00:00Z", last_used_at: null, revoked_at: null, ...extra });
const catalog = items => ({ items: items || [item()] });
const error = (message, status) => Object.assign(new Error(message), { status });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function harness(overrides = {}) {
  const calls = [];
  const client = { serviceApiKeys: async () => catalog(), createServiceApiKey: async payload => { calls.push(["issue", payload]); return { item: item(payload), api_key: "SYNTHETIC_ONE_TIME_SECRET" }; }, renameServiceApiKey: async (id, payload) => { calls.push(["rename", id, payload]); return item(payload); }, revokeServiceApiKey: async id => { calls.push(["revoke", id]); return item({ revoked_at: "2026-09-07T01:00:00Z" }); }, ...overrides };
  const controller = createServiceKeysController({ api: client, onChange() {} });
  const fill = () => { controller.update("name", " 합성 새 키 "); controller.update("source_system", "synthetic-collector"); controller.update("scopes", ["ingest", "review"]); };
  return { controller, calls, fill };
}

test("service key validation enforces names, case-insensitive reserved sources and least-privilege scopes", () => {
  const draft = { name: " 합성 이름 ! ", source_system: "sample-collector_1.v2", scopes: ["ingest"] };
  assert.equal(validateServiceKeyDraft(draft), "");
  for (const name of ["", " ", "a".repeat(121), "x\n", "x\t", "x\u202e", "x\u200b", "x\ud800"]) assert.ok(serviceKeyNameError(name));
  for (const source of ["", "sample ", " sample", "한글", "_sample", "a".repeat(121), "admin-ui", "ADMIN-UI", "waf-internal-test", "WAF-INTERNAL-model-test-x", "sample/path", "sample\n"]) assert.match(validateServiceKeyDraft({ ...draft, source_system: source }), /Source System/);
  for (const scopes of [[], ["admin"], ["ingest", "admin"], ["ingest", "ingest"], ["review", "review"], "ingest"]) assert.match(validateServiceKeyDraft({ ...draft, scopes }), /권한/);
  assert.equal(validateServiceKeyDraft({ ...draft, scopes: ["review"] }), "");
  assert.equal(validateServiceKeyDraft({ ...draft, scopes: ["review", "ingest"] }), "");
});

test("metadata and error helpers never echo server secrets or inherited object properties", () => {
  const sanitized = serviceKeyMetadata(item({ api_key: "NEVER_COPY_RAW", key_hash: "NEVER_COPY_HASH", extra: "NEVER_COPY", scopes: ["ingest", "admin"] }));
  assert.equal(Object.hasOwn(sanitized, "api_key"), false); assert.equal(Object.hasOwn(sanitized, "key_hash"), false); assert.deepEqual(sanitized.scopes, ["ingest"]);
  for (const code of ["SYNTHETIC_UPSTREAM_SECRET", "toString", "constructor", "__proto__"]) { const text = serviceKeyError(error(code, 500)); assert.equal(typeof text, "string"); assert.ok(!text.includes(code)); }
  assert.match(serviceKeyError(error("service_api_key_name_exists", 409)), /같은 이름/);
  assert.match(serviceKeyError(error("service_api_key_revoked", 409)), /활성화할 수 없습니다/);
  assert.match(serviceKeyError(error("x", 503), { issuing: true }), /자동으로 재발급하지/);
});

test("key creation has one explicit mutation, sanitizes metadata and keeps the raw key only until dismissed", async () => {
  const { controller, calls, fill } = harness(); await controller.refresh(); fill(); controller.update("api_key", "NOT_A_FIELD");
  assert.equal(await controller.issue(), true);
  assert.deepEqual(calls, [["issue", { name: "합성 새 키", source_system: "synthetic-collector", scopes: ["ingest", "review"] }]]);
  assert.equal(controller.getState().issued.api_key, "SYNTHETIC_ONE_TIME_SECRET");
  assert.ok(!JSON.stringify(controller.getState().catalog).includes("SYNTHETIC_ONE_TIME_SECRET"));
  assert.equal(await controller.issue(), false);
  await controller.refresh(); assert.equal(controller.getState().issued.api_key, "SYNTHETIC_ONE_TIME_SECRET");
  controller.closeIssued(); assert.equal(controller.getState().issued, null);
  await controller.refresh(); assert.equal(controller.getState().issued, null); assert.equal(calls.length, 1);
  controller.dispose();
});

test("a failing list refresh never hides the only successful issuance response", async () => {
  let reads = 0; const { controller, fill } = harness({ serviceApiKeys: async () => { if (++reads > 1) throw error("failed", 503); return catalog(); } });
  await controller.refresh(); fill(); assert.equal(await controller.issue(), true);
  assert.equal(controller.getState().issued.api_key, "SYNTHETIC_ONE_TIME_SECRET"); assert.equal(controller.getState().needsRefresh, true);
  controller.dispose(); assert.equal(controller.getState().issued, null);
});

test("ambiguous issuance failure never automatically retries or invents an empty success", async () => {
  let issues = 0; const { controller, fill } = harness({ createServiceApiKey: async () => { issues += 1; throw new Error("network_down"); } });
  await controller.refresh(); fill(); assert.equal(await controller.issue(), false);
  assert.equal(issues, 1); assert.equal(controller.getState().issued, null); assert.equal(controller.getState().draft.name, " 합성 새 키 "); assert.equal(controller.getState().needsRefresh, true);
  assert.match(controller.getState().error, /발급되었을 수/);
  assert.equal(await controller.issue(), false); await controller.refresh(); assert.equal(issues, 1);
  controller.dispose();
});

test("repeated issue cannot create duplicate keys and late success after unmount is not exposed", async () => {
  const result = deferred(); let issues = 0; const { controller, fill } = harness({ createServiceApiKey: async () => { issues += 1; return result.promise; } });
  await controller.refresh(); fill(); const issuing = controller.issue(); assert.equal(await controller.issue(), false); assert.equal(issues, 1);
  controller.dispose(); result.resolve({ item: item(), api_key: "SYNTHETIC_LATE_SECRET" }); assert.equal(await issuing, false); assert.equal(controller.getState().issued, null);
});

test("renaming submits only the name; revoked key errors retain the edit without changing scopes or source", async () => {
  const { controller, calls } = harness(); await controller.refresh(); controller.edit(item()); controller.updateName("새 이름"); assert.equal(await controller.rename(), true);
  assert.deepEqual(calls, [["rename", "synthetic-key-id", { name: "새 이름" }]]);
  controller.edit(item({ revoked_at: "2026-09-07T01:00:00Z" })); assert.equal(controller.getState().editing, null);
  controller.dispose();
  const denied = harness({ renameServiceApiKey: async () => { throw error("service_api_key_revoked", 409); } }); await denied.controller.refresh(); denied.controller.edit(item()); denied.controller.updateName("보존할 편집"); assert.equal(await denied.controller.rename(), false); assert.equal(denied.controller.getState().editName, "보존할 편집"); assert.match(denied.controller.getState().error, /폐기된/); denied.controller.dispose();
});

test("revoke is explicit and deduplicated, removes the issued raw key and has no restore action", async () => {
  const result = deferred(); let revokes = 0; const { controller, fill } = harness({ revokeServiceApiKey: async () => { revokes += 1; return result.promise; } });
  await controller.refresh(); fill(); await controller.issue(); const revoking = controller.revoke(item()); assert.equal(await controller.revoke(item()), false); assert.equal(revokes, 1);
  result.resolve(item({ revoked_at: "2026-09-07T01:00:00Z" })); assert.equal(await revoking, true); assert.equal(controller.getState().issued, null); assert.match(controller.getState().notice, /영구 폐기/);
  assert.equal(await controller.revoke(item({ revoked_at: "2026-09-07T01:00:00Z" })), false); controller.dispose();
});

test("lookup failures stay distinct from an empty key list and a successful retry removes the error", async () => {
  let failed = true; const { controller } = harness({ serviceApiKeys: async () => { if (failed) throw error("forbidden", 403); return catalog([]); } });
  assert.equal(await controller.refresh(), false); assert.equal(controller.getState().catalog, null); assert.match(controller.getState().error, /관리자/);
  failed = false; await controller.refresh(); assert.deepEqual(controller.getState().catalog.items, []); assert.equal(controller.getState().error, ""); controller.dispose();
});

test("DB-only list state discards accidental secret fields and out-of-order reads cannot regress metadata", async () => {
  const first = deferred(); let reads = 0; const rawCatalog = catalog([item({ api_key: "SYNTHETIC_LEAK" })]); rawCatalog.unexpected = "SYNTHETIC_UNEXPECTED_FIELD";
  const { controller } = harness({ serviceApiKeys: async () => ++reads === 1 ? first.promise : rawCatalog });
  const old = controller.refresh(); await controller.refresh(); first.resolve(catalog([item({ name: "stale" })])); await old;
  assert.equal(controller.getState().catalog.items[0].name, "합성 키"); assert.ok(!JSON.stringify(controller.getState().catalog).includes("SYNTHETIC_LEAK")); assert.deepEqual(Object.keys(controller.getState().catalog), ["items"]); controller.dispose();
});

test("DB-only catalog accepts items without any bootstrap field and never inspects removed metadata", async () => {
  const response = catalog([]);
  Object.defineProperty(response, "bootstrap", { get() { throw new Error("REMOVED_FIELD_MUST_NOT_BE_READ"); } });
  const { controller } = harness({ serviceApiKeys: async () => response });
  assert.equal(await controller.refresh(), true); assert.deepEqual(controller.getState().catalog, { items: [] }); assert.equal(controller.getState().error, ""); controller.dispose();
});

const bundled = buildSync({ entryPoints: [fileURLToPath(new URL("./ServiceApiKeys.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
const module = { exports: {} }; runInNewContext(bundled, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL });
const { ServiceApiKeysView } = module.exports;
const markup = state => renderToStaticMarkup(createElement(ServiceApiKeysView, { state, controller: {} }));

test("service key UI displays only DB-issued keys and one-time secrets with escaped text and no admin scope", () => {
  const html = markup({ ...emptyServiceKeysState(), catalog: catalog([item({ name: '<script>SYNTHETIC</script>' }), item({ id: "revoked", revoked_at: "2026-09-07T01:00:00Z" })]) });
  assert.match(html, /LLM 제공자/); assert.match(html, /만료일이 없습니다/); assert.match(html, /DB에 등록한 키만 사용/); assert.doesNotMatch(html, /WAF_BOOTSTRAP_API_KEY|기존 서비스 키|환경 키|service-key-bootstrap/); assert.match(html, /다시 활성화할 수 없습니다/); assert.match(html, /60초 간격/);
  assert.match(html, /&lt;script&gt;SYNTHETIC&lt;\/script&gt;/); assert.doesNotMatch(html, /<script>|발급된 API Key 원문|value="admin"/);
  const secret = markup({ ...emptyServiceKeysState(), catalog: catalog(), issued: { item: item(), api_key: "SYNTHETIC_ONCE_UI" } }); assert.match(secret, /SYNTHETIC_ONCE_UI/); assert.match(secret, /원문은 한 번만/); assert.match(secret, /키 복사/); assert.match(secret, /원문 닫기/); assert.match(secret, /<label for="issued-service-api-key">발급된 API Key 원문<\/label><textarea id="issued-service-api-key"/);
  const failureHtml = markup({ ...emptyServiceKeysState(), error: "조회 실패" }); assert.match(failureHtml, /role="alert">조회 실패/); assert.doesNotMatch(failureHtml, /발급된 서비스 API Key가 없습니다/);
});

test("service key API requests are administrator-only routes, no-store and issue exactly once", { concurrency: false }, async () => {
  const original = globalThis.fetch; const calls = []; globalThis.fetch = async (url, options) => { calls.push([url, options]); return new Response(JSON.stringify({}), { status: 200 }); };
  try {
    await api.serviceApiKeys(); await api.createServiceApiKey({ name: "synthetic", source_system: "synthetic", scopes: ["ingest"] }); await api.renameServiceApiKey("synthetic/id", { name: "new" }); await api.revokeServiceApiKey("synthetic/id");
    assert.equal(calls.length, 4); assert.equal(calls[0][0], "/api/v1/admin/service-api-keys"); assert.equal(calls[1][1].method, "POST"); assert.equal(calls[2][1].method, "PATCH"); assert.deepEqual(JSON.parse(calls[2][1].body), { name: "new" }); assert.equal(calls[3][0], "/api/v1/admin/service-api-keys/synthetic%2Fid/revoke"); assert.equal(calls[3][1].method, "POST"); assert.ok(calls.every(([, options]) => options.cache === "no-store" && options.credentials === "include"));
  } finally { globalThis.fetch = original; }
});
