import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { fetchReportFile, reportDownloadError, reportPath, reportTypes, saveReportFile } from "./analysisDownloads.js";

const id = "11111111-2222-4333-8444-555555555555";
const code = buildSync({ entryPoints: [fileURLToPath(new URL("./AnalysisDownloads.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
const module = { exports: {} };
runInNewContext(code, { module, exports: module.exports, require: createRequire(import.meta.url), AbortController });
const Downloads = module.exports.default;

test("only one analysis ID and two explicit binary formats can be requested", () => {
  assert.equal(reportPath(id, "pdf"), `/api/v1/analyses/${id}/report.pdf`);
  assert.equal(reportPath(id, "xlsx"), `/api/v1/analyses/${id}/report.xlsx`);
  for (const badId of ["../event", "//example.invalid", "a?include_raw=true", "", null]) assert.throws(() => reportPath(badId, "pdf"));
  for (const format of ["csv", "html", "event", "pdf?raw=true", "__proto__"]) assert.throws(() => reportPath(id, format));
});

test("downloads perform an authenticated no-store GET without invoking analysis or raw APIs", async () => {
  for (const format of ["pdf", "xlsx"]) {
    const controller = new AbortController(); let request;
    const blob = new Blob([format === "pdf" ? "%PDF-synthetic" : "PK-synthetic"], { type: reportTypes[format] });
    const result = await fetchReportFile(id, format, { signal: controller.signal, fetchImpl: async (path, options) => {
      request = { path, options }; return new Response(blob, { headers: { "Content-Type": reportTypes[format] } });
    } });
    assert.equal(await result.text(), await blob.text());
    assert.equal(request.path, reportPath(id, format));
    assert.equal(request.options.method, "GET"); assert.equal(request.options.body, undefined);
    assert.equal(request.options.credentials, "include"); assert.equal(request.options.cache, "no-store");
    assert.equal(request.options.signal, controller.signal);
  }
});

test("binary response validation rejects JSON, empty data, oversize and stale completion", async () => {
  await assert.rejects(fetchReportFile(id, "pdf", { fetchImpl: async () => new Response("{}", { headers: { "Content-Type": "application/json" } }) }), /invalid_report_response/);
  await assert.rejects(fetchReportFile(id, "pdf", { fetchImpl: async () => new Response("", { headers: { "Content-Type": "application/pdf" } }) }), /invalid_report_response/);
  await assert.rejects(fetchReportFile(id, "pdf", { fetchImpl: async () => new Response("x", { headers: { "Content-Type": "application/pdf", "Content-Length": String(10 * 1024 * 1024 + 1) } }) }), /report_too_large/);
  const controller = new AbortController();
  await assert.rejects(fetchReportFile(id, "pdf", { signal: controller.signal, fetchImpl: async () => {
    controller.abort(); return new Response("%PDF-test", { headers: { "Content-Type": "application/pdf" } });
  } }), { name: "AbortError" });
});

test("untrusted server errors never become analyst-facing HTML or raw diagnostic text", async () => {
  for (const [status, detail] of [[401, "private"], [403, "private"], [409, "report_not_final"], [413, "report_too_large"], [503, "<script>PRIVATE-SERVER-TEXT</script>"]]) {
    try {
      await fetchReportFile(id, "xlsx", { fetchImpl: async () => new Response(JSON.stringify({ detail }), { status, headers: { "Content-Type": "application/json" } }) });
      assert.fail("Expected rejection");
    } catch (error) {
      assert.equal(error.status, status);
      assert.doesNotMatch(error.message + reportDownloadError(error), /PRIVATE-SERVER-TEXT|<script>/);
      if (status === 409) assert.match(reportDownloadError(error), /최종 결과/);
      if (status === 413) assert.match(reportDownloadError(error), /부분 파일은 생성하지 않았습니다/);
    }
  }
});

test("file saving uses a bounded fixed filename, cleans up the link and revokes the blob URL", () => {
  const events = []; const anchor = { click() { events.push("click"); }, remove() { events.push("remove"); } };
  const documentImpl = { createElement(tag) { assert.equal(tag, "a"); return anchor; }, body: { append(value) { assert.equal(value, anchor); events.push("append"); } } };
  const urlImpl = { createObjectURL() { return "blob:synthetic"; }, revokeObjectURL(value) { events.push(value); } };
  let cleanup;
  saveReportFile(new Blob(["%PDF-test"]), id, "pdf", { documentImpl, urlImpl, schedule(callback, delay) { cleanup = callback; assert.equal(delay, 1000); } });
  assert.equal(anchor.href, "blob:synthetic"); assert.equal(anchor.download, `WAF-분석보고서-${id}.pdf`);
  assert.deepEqual(events, ["append", "click", "remove"]); cleanup(); assert.equal(events.at(-1), "blob:synthetic");
});

test("non-completed downloads are disabled and concise scope help retains excerpt, audit and limit warnings", () => {
  for (const status of ["pending", "processing", "failed"]) {
    const html = renderToStaticMarkup(createElement(Downloads, { id, status }));
    assert.equal((html.match(/disabled=""/g) || []).length, 2);
    assert.match(html, /분석이 완료되면/); assert.match(html, /근거 발췌 포함/);
    assert.match(html, /aria-label="다운로드 범위 설명"/); assert.doesNotMatch(html, /<details|role="tooltip"/);
  }
  const html = renderToStaticMarkup(createElement(Downloads, { id, status: "completed" }));
  assert.doesNotMatch(html, /disabled=""|https:\/\//);
  assert.match(html, /PDF 다운로드/); assert.match(html, /Excel 다운로드/);
  let prepared;
  function CaptureDownload() { prepared = Downloads({ id, status: "completed" }); return null; }
  renderToStaticMarkup(createElement(CaptureDownload));
  function findHelp(node) {
    if (Array.isArray(node)) return node.map(findHelp).find(Boolean);
    if (!node || typeof node !== "object") return null;
    return node.props?.label === "다운로드 범위" ? node : findHelp(node.props?.children);
  }
  const help = renderToStaticMarkup(findHelp(prepared).props.children);
  assert.match(help, /현재 분석 1건/); assert.match(help, /HTTP 전체 원문/); assert.match(help, /근거 발췌에는 내부 정보/);
  assert.match(help, /감사 이력/); assert.match(help, /80페이지/); assert.match(help, /초과하면 부분 파일을 만들지 않습니다/);
});
