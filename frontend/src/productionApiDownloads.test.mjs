import assert from "node:assert/strict";
import test from "node:test";
import { apiDocumentPdfPath, apiDocumentDownloadError, fetchApiDocumentPdf, saveApiDocumentPdf } from "./productionApiDownloads.js";

const schema = { version_id: "11111111-1111-4111-8111-111111111111", version_number: 3, content_hash: "a".repeat(64) };
const pdf = () => new Response("%PDF-1.4\nfixture\n%%EOF", { headers: { "content-type": "application/pdf" } });

test("API PDF download pins the displayed definition and makes only an authenticated GET", async () => {
  let calls = 0;
  const blob = await fetchApiDocumentPdf(schema, { fetchImpl: async (path, options) => {
    calls += 1; const url = new URL(path, "https://example.invalid");
    assert.equal(url.pathname, "/api/v1/production-api.pdf");
    assert.deepEqual(Object.fromEntries(url.searchParams), { expected_schema_version_id: schema.version_id, expected_schema_hash: schema.content_hash });
    assert.equal(options.method, "GET"); assert.equal(options.credentials, "include"); assert.equal(options.cache, "no-store"); assert.equal(options.body, undefined);
    return pdf();
  } });
  assert.equal(calls, 1); assert.match(await blob.text(), /^%PDF-/);
  for (const invalid of [null, {}, { ...schema, version_id: "https://external.invalid" }, { ...schema, content_hash: "not-a-hash" }]) assert.throws(() => apiDocumentPdfPath(invalid));
});

test("API PDF errors are safe, including stale schema and output limits", async () => {
  for (const [status, detail] of [[409, "input_schema_document_changed"], [413, "production_api_pdf_too_large"], [401, "SECRET UPSTREAM"], [503, "SECRET UPSTREAM"]]) {
    await assert.rejects(fetchApiDocumentPdf(schema, { fetchImpl: async () => new Response(JSON.stringify({ detail }), { status, headers: { "content-type": "application/json" } }) }), error => {
      const message = apiDocumentDownloadError(error); assert.ok(!message.includes("SECRET"));
      if (status === 409) assert.match(message, /스키마가 변경.*새로고침/);
      if (status === 413) assert.match(message, /한도/);
      if (status === 401) assert.match(message, /로그인/);
      return true;
    });
  }
});

test("API PDF rejects wrong media, empty or invalid files, oversize responses and aborted requests", async () => {
  for (const response of [new Response("<html>private</html>"), new Response("not PDF", { headers: { "content-type": "application/pdf" } }), new Response("", { headers: { "content-type": "application/pdf" } }), new Response("%PDF-", { headers: { "content-type": "application/pdf", "content-length": String(11 * 1024 * 1024) } })]) {
    await assert.rejects(fetchApiDocumentPdf(schema, { fetchImpl: async () => response }));
  }
  const controller = new AbortController(); controller.abort();
  await assert.rejects(fetchApiDocumentPdf(schema, { signal: controller.signal, fetchImpl: async () => pdf() }), { name: "AbortError" });
});

test("API PDF save uses a safe versioned filename and frees its temporary URL", () => {
  const calls = []; const anchor = { click() { calls.push("click"); }, remove() { calls.push("remove"); } };
  const options = { documentImpl: { createElement: () => anchor, body: { append() { calls.push("append"); } } }, urlImpl: { createObjectURL: () => "blob:fixture", revokeObjectURL: url => calls.push(url) }, schedule: callback => callback() };
  saveApiDocumentPdf(new Blob(["%PDF-"]), 3, options);
  assert.equal(anchor.download, "Production_API_v0.2.0_schema-v3.pdf");
  assert.deepEqual(calls, ["append", "click", "remove", "blob:fixture"]);
  assert.throws(() => saveApiDocumentPdf(new Blob(), "../unsafe", options));
});
