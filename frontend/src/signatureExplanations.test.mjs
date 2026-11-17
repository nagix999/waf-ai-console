import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "./api.js";
import { applyAppRoute, readAppHash, writeAppHash } from "./appRoutes.js";
import { consoleDestination, consoleLocation } from "./consoleNavigation.js";
import { candidateIssue, candidatePrefill, defaultCandidate } from "./candidateConfiguration.js";
import { configurationChanges } from "./testConfigurationIdentity.js";
import { emptySignatureExplanation, filteredSignatureEntries, signatureDraftIssue, signatureEntryIssue,
  signatureExplanationDraft, signatureExplanationLimits, signatureVersionPayload } from "./signatureExplanations.js";

const entry = { waf_vendor: "Example WAF", signature: "Example detection", signature_explanation: "A synthetic detection description. <script>never execute</script>", reference_source: "https://example.test/vendor-reference" };
const version = { id: "10000000-0000-4000-8000-000000000001", version_no: 2, name: "Example references", entries: [entry] };
function components() {
  const source = buildSync({ entryPoints: [fileURLToPath(new URL("./SignatureExplanations.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
  const module = { exports: {} }; runInNewContext(source, { module, exports: module.exports, require: createRequire(import.meta.url), console, URL, TextEncoder }); return module.exports;
}

test("signature explanation edits clone immutable versions and submit only contract fields", () => {
  const source = structuredClone(version), draft = signatureExplanationDraft(source);
  assert.equal(draft.parent_id, version.id); assert.equal(draft.name, "");
  draft.entries[0].signature_explanation = "Updated reference context";
  assert.deepEqual(source, version);
  assert.deepEqual(emptySignatureExplanation(), { waf_vendor: "", signature: "", signature_explanation: "", reference_source: "" });
  assert.equal(signatureDraftIssue(draft), "version_required");
  draft.name = " Updated version "; draft.change_reason = " Description improved ";
  assert.equal(signatureDraftIssue(draft), "");
  const payload = signatureVersionPayload({ ...draft, unrelated: "not sent", entries: [{ ...draft.entries[0], id: "not sent" }] });
  assert.equal(payload.name, "Updated version"); assert.equal(payload.change_reason, "Description improved");
  assert.equal(payload.entries[0].id, undefined); assert.equal(payload.unrelated, undefined);
  assert.equal(signatureDraftIssue({ ...draft, entries: [] }), "");
  assert.deepEqual(signatureExplanationDraft().entries, []);
});

test("signature explanation limits reject empty, duplicate and oversized drafts", () => {
  assert.equal(signatureEntryIssue(emptySignatureExplanation()), "required");
  assert.equal(signatureEntryIssue({ ...entry, signature: "  " }), "required");
  assert.equal(signatureEntryIssue({ ...entry, waf_vendor: " example waf " }, [entry]), "duplicate");
  assert.equal(signatureEntryIssue(entry, [entry], 0), "");
  assert.equal(signatureEntryIssue({ ...entry, signature: "example detection" }, [entry]), "");
  assert.equal(signatureEntryIssue({ ...entry, signature_explanation: "x".repeat(2001) }), "length");
  assert.equal(signatureEntryIssue({ ...entry, reference_source: "x".repeat(1001) }), "length");
  const draft = { ...signatureExplanationDraft(version), name: "Bounded", change_reason: "Test" };
  assert.equal(signatureDraftIssue({ ...draft, entries: Array.from({ length: signatureExplanationLimits.entries + 1 }, () => entry) }), "length");
  assert.equal(signatureDraftIssue({ ...draft, entries: [entry, { ...entry }] }), "duplicate");
  const large = Array.from({ length: 90 }, (_, index) => ({ ...entry, signature: `Detection ${index}`, signature_explanation: "가".repeat(2000) }));
  assert.equal(signatureDraftIssue({ ...draft, entries: large }), "bytes");
});

test("entry filters retain original indices across vendor, query and pagination", () => {
  const entries = [entry, { ...entry, waf_vendor: "Other vendor", signature: "Second detection" }];
  assert.equal(filteredSignatureEntries(entries, "Other vendor")[0]._index, 1);
  assert.equal(filteredSignatureEntries(entries, "", "SECOND")[0]._index, 1);
  assert.equal(filteredSignatureEntries(entries, "Example WAF", "second").length, 0);
  assert.equal(filteredSignatureEntries(entries, "", "synthetic").length, 2);
  assert.equal(entries[0].id, undefined);
});

test("signature explanation settings have a stable URL and sidebar location", () => {
  const initial = { page: "dashboard", testResults: {} }, next = consoleDestination(initial, "signatureExplanations");
  assert.equal(writeAppHash(next), "#configure/signature-explanations");
  assert.equal(consoleLocation(next).group, "configure");
  assert.equal(consoleLocation(applyAppRoute(initial, readAppHash(writeAppHash(next)))).item, "signatureExplanations");
  assert.equal(readAppHash("#settings/signature-explanations").tab, "signature-explanations");
  assert.equal(readAppHash("#configure/signature-explanations/private-query"), null);
});

test("candidate selection is optional, pinned and included in Production comparisons", () => {
  const agents = { profiles: [{ id: "model", can_assign: true }], assignments: { test: { primary_profile_id: "model" } } };
  const prompts = { active_version_id: "prompt", items: [{ id: "prompt" }] }, schemas = { active_version_id: "schema", items: [{ id: "schema" }] };
  const catalog = { agents, prompts, schemas, signatureExplanations: { items: [version] } };
  const candidate = defaultCandidate(agents, prompts, schemas);
  assert.equal(candidateIssue(catalog, candidate), "");
  assert.equal(candidateIssue(catalog, { ...candidate, signature_explanation_version_id: version.id }), "");
  assert.match(candidateIssue(catalog, { ...candidate, signature_explanation_version_id: "missing" }), /설명집/);
  assert.equal(candidatePrefill({ ...candidate, signature_explanation_version_id: version.id }, null).signature_explanation_version_id, version.id);
  assert.deepEqual(configurationChanges({}, { signature_explanations: { version_id: null } }), []);
  assert.deepEqual(configurationChanges({}, { signature_explanations: { version_id: version.id } }), ["signature_explanations"]);
});

test("reference text is escaped, full explanations stay in detail, and saving never implies activation", () => {
  const { SignatureExplanationContent, SignatureExplanationNotice, SignatureExplanationEntries, SignatureExplanationEntryForm } = components();
  const detail = renderToStaticMarkup(createElement(SignatureExplanationContent, { entry }));
  assert.match(detail, /&lt;script&gt;never execute&lt;\/script&gt;/); assert.doesNotMatch(detail, /<script|href="https/);
  const notice = renderToStaticMarkup(createElement(SignatureExplanationNotice));
  assert.match(notice, /저장은 운영 반영이 아닙니다/); assert.match(notice, /개별 요청의 정답이나 공격 근거가 아닙니다/);
  const list = renderToStaticMarkup(createElement(SignatureExplanationEntries, { entries: [entry], vendor: "", query: "", offset: 0, editing: false }));
  assert.match(list, /시그니처 설명 목록/); assert.match(list, /scope="col"/); assert.match(list, /signature-catalog-preview/);
  assert.doesNotMatch(list, /https:\/\/example.test\/vendor-reference|data-tooltip/);
  const form = renderToStaticMarkup(createElement(SignatureExplanationEntryForm, { value: emptySignatureExplanation() }));
  assert.match(form, /maxLength="2000"/); assert.match(form, /실제 로그, 인증정보/); assert.doesNotMatch(form, /value="Example/);
});

test("signature reference API keeps match contents in POST body and only version IDs in paths", async () => {
  const oldFetch = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => ({}) }; };
  try {
    await api.signatureExplanations({ limit: 25, offset: 25 });
    await api.signatureExplanation(version.id);
    await api.createSignatureExplanation({ name: "Fixture", change_reason: "Fixture", entries: [entry] });
    await api.matchSignatureExplanation({ version_id: version.id, waf_vendor: "PRIVATE_VENDOR", signature: "PRIVATE_SIGNATURE" });
    assert.equal(calls[0].url, "/api/v1/admin/signature-explanations?limit=25&offset=25");
    assert.equal(calls[1].url, `/api/v1/admin/signature-explanations/${version.id}`);
    assert.equal(calls[2].options.method, "POST");
    assert.equal(calls[3].url, "/api/v1/admin/signature-explanations/match");
    assert.equal(JSON.parse(calls[3].options.body).signature, "PRIVATE_SIGNATURE");
    assert.ok(calls.every(call => !call.url.includes("PRIVATE_") && !call.url.includes("activate") && !call.url.includes("promote")));
  } finally { globalThis.fetch = oldFetch; }
});
