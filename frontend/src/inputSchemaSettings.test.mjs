import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { FIELD_TYPES, SAMPLE_EVENT, changeFieldType, createSchemaSettingsController, draftPayload, editSchemaField, newSchemaField, parseSchemaSample, schemaDraft, schemaFieldDiff, schemaSaveError } from "./inputSchemaSettings.js";
import { api } from "./api.js";

const base = { name: "event_id", description: "외부 이벤트 식별자", type: "string", required: true, nullable: false, min_length: 1, max_length: 255 };
const versions = [
  { id: "schema-1", version_number: 1, name: "기본", fields: [base], content_hash: "hash-one" },
  { id: "schema-2", version_number: 2, name: "개선", fields: [{ ...base, description: "새 설명" }], content_hash: "hash-two" },
];
function harness(overrides = {}) {
  let activeId = "schema-1"; let revision = 1; let state; let clock = 1000; let timer;
  const calls = [];
  const mock = {
    inputSchemas: async () => ({ items: versions.map(({ fields, ...summary }) => summary), default_fields: [base], active_version_id: activeId, revision }),
    inputSchemaHistory: async () => ({ items: [] }),
    inputSchema: async id => versions.find(item => item.id === id),
    createInputSchema: async payload => { calls.push(["save", payload]); return versions[1]; },
    validateInputSchema: async (id, event) => { calls.push(["validate", id, event]); return { valid: true, issues: [], validation_token: "synthetic-validation-token", expires_in_seconds: 300 }; },
    activateInputSchema: async (id, payload) => { calls.push(["activate", id, payload]); activeId = id; revision++; return { active_version_id: id, revision }; },
    ...overrides,
  };
  const controller = createSchemaSettingsController({ api: mock, onChange: value => { state = value; }, now: () => clock, schedule: callback => { timer = callback; return 1; }, cancel: () => { timer = null; } });
  return { controller, mock, calls, get state() { return state; }, expire: () => { clock += 300001; timer?.(); }, setClock: value => { clock = value; } };
}
function bundle(path) {
  const result = buildSync({ entryPoints: [fileURLToPath(new URL(path, import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty", ".md": "text" }, logLevel: "silent" }).outputFiles[0].text;
  const module = { exports: {} };
  runInNewContext(result, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, Date, setTimeout, clearTimeout });
  return module.exports;
}
const components = bundle("./InputSchemaSettings.jsx");
const app = bundle("./App.jsx");
const production = bundle("./ProductionApi.jsx");
const render = (component, props) => renderToStaticMarkup(createElement(component, props));

test("schema clones preserve original nested metadata and emit no editor-only properties", () => {
  const source = { id: "schema-1", fields: [base, { name: "tags", description: "태그", type: "array", required: false, nullable: true, items: { type: "string", nullable: false, max_length: 12 } }] };
  const before = JSON.stringify(source); const draft = schemaDraft(source);
  draft.name = " 새 버전 "; draft.change_note = " 설명 "; draft.fields[1]._items = '{"type":"integer","nullable":false}';
  const payload = draftPayload(draft);
  assert.equal(payload.parent_id, source.id); assert.equal(payload.name, "새 버전");
  assert.equal(payload.fields[1].items.type, "integer"); assert.equal(JSON.stringify(source), before);
  assert.equal(Object.keys(payload.fields[1]).some(key => key.startsWith("_")), false);
});
test("custom scalar, array and object constraints keep false and zero and reset incompatible types", () => {
  const draft = schemaDraft({ fields: [ { name: "count", type: "integer", nullable: false, minimum: 0, maximum: 5, enum: [0, 1] }, { name: "enabled", type: "boolean", enum: [false] }, { name: "meta", type: "object", properties: [{ ...base, name: "region" }] }] });
  Object.assign(draft, { name: "types", change_note: "synthetic" });
  const payload = draftPayload(draft);
  assert.equal(payload.fields[0].minimum, 0); assert.deepEqual(payload.fields[1].enum, [false]);
  assert.equal(payload.fields[2].properties[0].name, "region");
  const changed = changeFieldType(draft.fields[0], "object");
  assert.equal(changed.minimum, undefined); assert.equal(changed._enum, "");
  assert.deepEqual(FIELD_TYPES, ["string", "integer", "number", "boolean", "object", "array"]);
  assert.equal(newSchemaField().required, false);
});
test("schema editor catches duplicate names, malformed JSON, non-finite limits and reversed limits", () => {
  const create = fields => ({ name: "n", change_note: "c", parent_id: null, fields: fields.map(editSchemaField) });
  for (const fields of [[base, base], [{ ...base, name: "bad-name" }], [{ ...base, min_length: 4, max_length: 2 }], [{ ...base, max_length: "Infinity" }], [{ ...base, max_length: 1.5 }]]) assert.throws(() => draftPayload(create(fields)));
  const draft = create([base]); draft.fields[0]._enum = "not-json"; assert.throws(() => draftPayload(draft), /JSON/);
  draft.fields[0]._enum = "{}"; assert.throws(() => draftPayload(draft), /배열/);
  draft.name = " "; assert.throws(() => draftPayload(draft), /버전 이름/);
});
test("field comparison includes descriptions and recursive definitions but ignores JSON key order", () => {
  assert.deepEqual(schemaFieldDiff([base], [{ ...base }]), []);
  assert.deepEqual(schemaFieldDiff([base], [{ nullable: false, required: true, type: "string", description: base.description, name: base.name, max_length: 255, min_length: 1, enum: null }]), []);
  assert.equal(schemaFieldDiff([base], [{ ...base, description: "바뀐 설명" }])[0].kind, "changed");
  assert.equal(schemaFieldDiff([], [base])[0].kind, "added");
  assert.equal(schemaFieldDiff([base], [])[0].kind, "removed");
  assert.equal(schemaFieldDiff([{ ...base, type: "array", items: { type: "string" } }], [{ ...base, type: "array", items: { type: "integer" } }])[0].kind, "changed");
});
test("sample parser accepts exactly one object and never reflects invalid sample text in errors", () => {
  assert.equal(parseSchemaSample(SAMPLE_EVENT).waf_action, "A");
  for (const value of ["[]", "null", '"synthetic-secret"', '{"synthetic-secret":', " ".repeat(2_200_001)]) {
    assert.throws(() => parseSchemaSample(value), error => !error.message.includes("synthetic-secret"));
  }
});
test("schema activation needs a selected nonactive version, fresh valid sample and acknowledgement", async () => {
  const h = harness(); await h.controller.refresh();
  assert.equal(h.state.selected.id, "schema-1"); assert.equal(await h.controller.activate(), false);
  await h.controller.select("schema-2"); await h.controller.validate(SAMPLE_EVENT);
  assert.equal(Boolean(h.controller.canActivate()), false); h.controller.acknowledge(true);
  assert.equal(h.controller.canActivate(), true); assert.equal(await h.controller.activate(), true);
  assert.deepEqual(h.calls[1], ["activate", "schema-2", { expected_revision: 1, validation_token: "synthetic-validation-token" }]);
  assert.equal(h.state.catalog.active_version_id, "schema-2"); assert.equal(h.state.catalog.revision, 2); assert.equal(h.state.validation, null);
  h.controller.dispose();
});
test("rollback uses a previously saved version through the same fresh validation path", async () => {
  const h = harness(); await h.controller.refresh(); await h.controller.select("schema-2"); await h.controller.validate(SAMPLE_EVENT); h.controller.acknowledge(true); await h.controller.activate();
  await h.controller.select("schema-1"); assert.equal(await h.controller.activate(), false);
  await h.controller.validate(SAMPLE_EVENT); h.controller.acknowledge(true); assert.equal(await h.controller.activate(), true);
  assert.equal(h.state.catalog.active_version_id, "schema-1"); assert.equal(h.state.catalog.revision, 3); h.controller.dispose();
});
test("sample edits, selection, refresh and token expiry invalidate activation", async () => {
  const h = harness(); await h.controller.refresh(); await h.controller.select("schema-2");
  for (const invalidate of [() => h.controller.clearValidation(), () => h.expire(), () => h.controller.refresh(), () => h.controller.select("schema-1")]) {
    await h.controller.select("schema-2"); await h.controller.validate(SAMPLE_EVENT); h.controller.acknowledge(true); assert.equal(h.controller.canActivate(), true);
    await invalidate(); assert.equal(Boolean(h.controller.canActivate()), false); assert.equal(h.state.validation, null);
  }
  h.controller.dispose();
});
test("clock boundary blocks expired activation even before its timer is delivered", async () => {
  const h = harness(); await h.controller.refresh(); await h.controller.select("schema-2"); await h.controller.validate(SAMPLE_EVENT); h.controller.acknowledge(true);
  h.setClock(301001); assert.equal(Boolean(h.controller.canActivate()), false); assert.equal(await h.controller.activate(), false); h.controller.dispose();
});
test("invalid samples and missing or malformed token expiry never enable activation", async () => {
  for (const result of [{ valid: false, issues: [{ field: "event_id", type: "missing", message: "필수 필드" }] }, { valid: true, validation_token: "token" }, { valid: true, validation_token: "token", expires_in_seconds: 0 }]) {
    const h = harness({ validateInputSchema: async () => result }); await h.controller.refresh(); await h.controller.select("schema-2"); await h.controller.validate(SAMPLE_EVENT); h.controller.acknowledge(true);
    assert.equal(Boolean(h.controller.canActivate()), false); h.controller.dispose();
  }
});
test("revision conflict or uncertain activation response require a refresh and renewed validation", async () => {
  for (const status of [409, 503]) {
    const h = harness({ activateInputSchema: async () => { throw Object.assign(new Error("synthetic-private-body"), { status }); } });
    await h.controller.refresh(); await h.controller.select("schema-2"); await h.controller.validate(SAMPLE_EVENT); h.controller.acknowledge(true); await h.controller.activate();
    assert.equal(h.state.needsRefresh, true); assert.equal(h.state.validation, null); assert.doesNotMatch(h.state.error, /synthetic-private/);
    await h.controller.validate(SAMPLE_EVENT); assert.equal(h.state.validation, null); h.controller.dispose();
  }
});
test("save stores a new version only and cannot implicitly validate or activate", async () => {
  const h = harness(); await h.controller.refresh(); const draft = schemaDraft(versions[0]); draft.name = "new"; draft.change_note = "change";
  assert.equal((await h.controller.save(draft)).id, "schema-2"); assert.equal(h.calls.length, 1); assert.equal(h.calls[0][0], "save");
  assert.equal(h.state.catalog.active_version_id, "schema-1"); assert.equal(h.state.selected.id, "schema-2"); assert.equal(h.state.validation, null); h.controller.dispose();
});
test("definite schema format errors remain editable while uncertain save outcomes require catalog refresh", async () => {
  for (const status of [422, 503]) {
    const h = harness({ createInputSchema: async () => { throw Object.assign(new Error("synthetic-private-body"), { status }); } }); await h.controller.refresh();
    const draft = schemaDraft(versions[0]); draft.name = "new"; draft.change_note = "change"; await h.controller.save(draft);
    assert.equal(h.state.needsRefresh, status === 503); assert.doesNotMatch(h.state.error, /synthetic-private/); h.controller.dispose();
  }
  assert.match(schemaSaveError(new Error("input_schema_reserved_field")), /정답 필드/);
});
test("late validation after invalidation or disposal cannot resurrect an activation token", async () => {
  let release;
  const h = harness({ validateInputSchema: () => new Promise(resolve => { release = resolve; }) }); await h.controller.refresh(); await h.controller.select("schema-2");
  const pending = h.controller.validate(SAMPLE_EVENT); h.controller.clearValidation(); release({ valid: true, validation_token: "token", expires_in_seconds: 300 }); await pending;
  assert.equal(h.state.validation, null); assert.equal(h.state.busy, "");
  const second = h.controller.validate(SAMPLE_EVENT); h.controller.dispose(); const before = h.state;
  release({ valid: true, validation_token: "token", expires_in_seconds: 300 }); await second; assert.equal(h.state, before);
});
test("API bindings isolate schema metadata from event submission and use no-store reads", async () => {
  const original = globalThis.fetch; const requests = [];
  globalThis.fetch = async (path, options) => { requests.push([path, options]); return { ok: true, status: 200, json: async () => ({}) }; };
  try {
    await api.productionApi(); await api.inputSchemas(); await api.inputSchema("version / 2"); await api.inputSchemaHistory();
    await api.createInputSchema({ fields: [base] }); await api.validateInputSchema("version / 2", { event_id: "synthetic" }); await api.activateInputSchema("version / 2", { expected_revision: 4, validation_token: "synthetic-token" });
    assert.equal(requests[0][0], "/api/v1/production-api"); assert.match(requests[2][0], /version%20%2F%202$/);
    for (const [, options] of requests.slice(0, 4)) { assert.equal(options.credentials, "include"); assert.equal(options.cache, "no-store"); }
    assert.deepEqual(JSON.parse(requests[5][1].body), { event: { event_id: "synthetic" } });
    assert.deepEqual(JSON.parse(requests[6][1].body), { expected_revision: 4, validation_token: "synthetic-token" });
    assert.equal(requests.some(([path]) => /\/analyses|model-profiles|test-runs/.test(path)), false);
  } finally { globalThis.fetch = original; }
});
test("settings offers schema management without displacing existing model, prompt or key controls", () => {
  const html = render(app.Settings, { onProductionChange() {}, onViewDataset() {} });
  for (const label of ["LLM 프로필", "프롬프트", "입력 스키마", "Internal Egress", "서비스 API Key"]) assert.ok(html.includes(label));
  const schema = render(components.default);
  assert.match(schema, /설명을 LLM 입력에 추가하지 않습니다/); assert.match(schema, /이미 접수된 분석과 테스트/); assert.match(schema, /운영 적용 이력/);
});
test("schema fields and nested hostile strings render as inert text, not links or HTML", () => {
  const hostile = '<img src=x onerror="alert(1)">';
  const html = render(components.SchemaFields, { fields: [{ ...base, description: hostile, enum: [hostile], properties: [{ ...base, description: hostile }] }] });
  assert.doesNotMatch(html, /<img|<script|javascript:/); assert.match(html, /&lt;img/); assert.match(html, /null 불가/);
  const diff = render(components.SchemaDiff, { before: [base], after: [{ ...base, description: hostile }] }); assert.doesNotMatch(diff, /<img/); assert.match(diff, /변경 전/); assert.match(diff, /변경 후/);
});
test("technical metadata shows historical version only and never guesses current definitions for legacy analyses", () => {
  const legacy = render(components.InputSchemaMetadata, { metadata: null }); assert.match(legacy, /현재 설정으로 추정하지 않습니다/);
  const html = render(components.InputSchemaMetadata, { metadata: { version_id: "schema-1", version_number: 1, content_hash: "hash-one", field_count: 11, selection_origin: "ingest", payload: "synthetic-private-payload" } });
  assert.match(html, /11개 필드/); assert.match(html, /당시 필드 정의 보기/); assert.match(html, /hash-one/); assert.doesNotMatch(html, /synthetic-private-payload/);
});
test("Production documentation waits for active server definitions and never offers a stale build-time schema download", () => {
  const html = render(production.default); assert.match(html, /현재 운영 스키마로 정의서를 불러오는 중/); assert.doesNotMatch(html, /download=|api-doc-layout/);
  const source = readFileSync(new URL("./ProductionApi.jsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /\.md\?raw|빌드 시점/); assert.match(source, /api\.productionApi/); assert.match(source, /new Blob\(\[reference\.markdown\]/); assert.match(source, /parseApiDocument\(reference\?\.markdown/); assert.match(source, /URL\.revokeObjectURL/);
});
