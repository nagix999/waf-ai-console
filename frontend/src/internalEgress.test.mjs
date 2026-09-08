import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { api } from "./api.js";
import {
  allowedInternalTarget, canonicalPrivateIP, createInternalEgressController, emptyInternalEgressState,
  internalEgressError, internalEndpoint, internalTargetAddress, internalTargetURL,
  validateInternalEgressDraft, validInternalPort, vllmTargetError,
} from "./internalEgress.js";

const target = (extra = {}) => ({ id: "synthetic-target", ip_address: "10.0.0.10", port: 8000, description: "합성 내부 서버", revision: 1, in_use_profiles: [], ...extra });
const failure = (code, status) => Object.assign(new Error(code), { status });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function harness(overrides = {}) {
  const calls = [];
  const client = {
    internalEgress: async () => [target()],
    createInternalEgress: async (payload) => { calls.push(["POST", payload]); return target(payload); },
    updateInternalEgress: async (id, payload) => { calls.push(["PUT", id, payload]); return target(payload); },
    deleteInternalEgress: async (id, revision) => { calls.push(["DELETE", id, revision]); },
    ...overrides,
  };
  const controller = createInternalEgressController({ api: client, onChange() {} });
  return { controller, client, calls };
}

test("internal IP validation allows only explicit RFC1918 IPv4 and canonical ULA IPv6", () => {
  for (const value of ["10.0.0.0", "10.255.255.255", "172.16.0.0", "172.31.255.255", "192.168.0.0", "192.168.255.255"]) assert.equal(canonicalPrivateIP(value), value);
  assert.equal(canonicalPrivateIP("FD12:3456:0000:0000:0000:0000:0000:0010"), "fd12:3456::10");
  assert.equal(canonicalPrivateIP("fc00::"), "fc00::");
  assert.equal(canonicalPrivateIP("fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"), "fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff");
  assert.equal(canonicalPrivateIP("fd00::192.0.2.1"), "fd00::c000:201");
  for (const value of ["vllm.internal", "localhost", "127.0.0.1", "169.254.1.1", "192.0.2.1", "100.64.0.1", "172.15.255.255", "172.32.0.0", "192.169.0.1", "0.0.0.0", "224.0.0.1", "8.8.8.8", "::1", "::", "fe80::1", "fe00::1", "fbff::1", "2001:db8::1", "::ffff:10.0.0.1", "fd00::1%eth0", "[fd00::1]", "10.0.0.1/32", "10.0.0.256", "10.1", "167772161", "0x0a000001", "010.0.0.1", "10.00.0.1", "10.0.0.1 ", " 10.0.0.1", "fd:::1", "fd00::1\n"]) assert.equal(canonicalPrivateIP(value), null, value);
});

test("endpoint matching compares canonical literal IP and effective port, with the server's limited paths", () => {
  const items = [target(), target({ id: "ipv6", ip_address: "fd00::10", port: 443 }), target({ id: "http-default", port: 80 })];
  for (const path of ["", "/", "/v1", "/v1/"]) assert.equal(allowedInternalTarget(`http://10.0.0.10:8000${path}`, items)?.id, "synthetic-target");
  assert.equal(allowedInternalTarget("https://[FD00:0:0:0:0:0:0:10]/v1", items)?.id, "ipv6");
  assert.equal(allowedInternalTarget("http://10.0.0.10/v1", items)?.id, "http-default");
  for (const value of ["http://vllm.internal:8000/v1", "http://10.0.0.10:8001/v1", "http://10.0.0.11:8000/v1", "http://167772170:8000/v1", "http://012.0.0.10:8000/v1", "http://10.0.0.10:8000/other", "http://10.0.0.10:8000/v1//", "http://10.0.0.10:8000/v1?x=1", "http://10.0.0.10:8000/v1#x", "http://user@10.0.0.10:8000/v1", "http://10.0.0.10:8000\\@example.invalid/v1", "http://10.0.0.10:8000/v1\n", "ftp://10.0.0.10:8000/v1"]) assert.equal(allowedInternalTarget(value, items), null, value);
  assert.deepEqual(internalEndpoint("https://[fd00::10]/v1"), { ip_address: "fd00::10", port: 443 });
  assert.equal(internalTargetAddress(items[1]), "[fd00::10]:443");
  assert.equal(internalTargetURL(items[1], "https://10.0.0.1:8000/v1"), "https://[fd00::10]:443/v1");
  assert.equal(internalTargetURL(target()), "http://10.0.0.10:8000/v1");
});

test("draft validation and fixed error mapping reject malformed ports and hidden description controls", () => {
  for (const port of [1, "8000", 65535]) assert.equal(validInternalPort(port), true);
  for (const port of [0, -1, 65536, "1.5", "1e3", "0x50", "", " 80", "080", null, Infinity]) assert.equal(validInternalPort(port), false);
  assert.equal(validateInternalEgressDraft({ ip_address: " fd00::1 ", port: "8000", description: " 합성 서버 " }), "");
  for (const value of ["x\n", "x\u0000", "x\u0085", "x\u202e", "x\u200b", "x\ud800"]) assert.match(validateInternalEgressDraft({ ip_address: "10.0.0.1", port: "8000", description: value }), /제어문자/);
  assert.match(validateInternalEgressDraft({ ip_address: "10.0.0.1", port: "8000", description: "가".repeat(501) }), /500자/);
  for (const message of ["toString", "__proto__", "constructor", "UNTRUSTED_SERVER_BODY"]) { const text = internalEgressError(new Error(message)); assert.equal(typeof text, "string"); assert.ok(!text.includes(message)); }
  assert.match(internalEgressError(failure("internal_egress_target_in_use", 409)), /비활성화/);
  assert.match(internalEgressError(failure("internal_egress_changed", 409)), /입력은 유지/);
  assert.match(internalEgressError(failure("x", 403)), /관리자/);
});

test("vLLM target gating distinguishes loading, failed lookup, empty list, legacy hostname and allowed target", () => {
  assert.match(vllmTargetError("", undefined), /불러오는 중/);
  assert.match(vllmTargetError("", { loading: false, items: null, error: "failed" }), /확인하지 못/);
  assert.match(vllmTargetError("", { items: [] }), /먼저 등록/);
  assert.match(vllmTargetError("http://vllm.internal:8000/v1", { items: [target()] }), /hostname/);
  assert.match(vllmTargetError("http://10.0.0.11:8000/v1", { items: [target()] }), /등록되어 있지/);
  assert.equal(vllmTargetError("http://10.0.0.10:8000/v1", { items: [target()] }), "");
});

test("CRUD controller submits only canonical fields and the current expected revision without LLM calls", async () => {
  const { controller, calls } = harness();
  await controller.refresh();
  controller.update("ip_address", " FD00:0:0:0:0:0:0:10 "); controller.update("port", "443"); controller.update("description", " 합성 설명 "); controller.update("api_key", "NEVER_COPY");
  assert.equal(await controller.save(), true);
  assert.deepEqual(calls[0], ["POST", { ip_address: "fd00::10", port: 443, description: "합성 설명" }]);
  controller.edit(target({ revision: 7 })); controller.update("description", "편집 설명");
  assert.equal(await controller.save(), true);
  assert.deepEqual(calls[1], ["PUT", "synthetic-target", { ip_address: "10.0.0.10", port: 8000, description: "편집 설명", expected_revision: 7 }]);
  assert.equal(await controller.remove(target({ revision: 9 })), true);
  assert.deepEqual(calls[2], ["DELETE", "synthetic-target", 9]);
  controller.dispose();
});

test("concurrent save is rejected; changed revision refreshes without losing input or automatically retrying", async () => {
  const write = deferred(); let revision = 1; let puts = 0;
  const { controller } = harness({ internalEgress: async () => [target({ revision, description: revision === 1 ? "이전" : "다른 관리자 설명" })], updateInternalEgress: async () => { puts += 1; return write.promise; } });
  await controller.refresh(); controller.edit(controller.getState().items[0]); controller.update("description", "저장할 초안");
  const saving = controller.save();
  assert.equal(await controller.save(), false); assert.equal(puts, 1);
  revision = 2; write.reject(failure("internal_egress_changed", 409)); assert.equal(await saving, false);
  assert.equal(controller.getState().draft.description, "저장할 초안");
  assert.equal(controller.getState().editing.revision, 1); assert.equal(controller.getState().items[0].revision, 2); assert.equal(controller.getState().conflict, true);
  assert.match(controller.getState().error, /입력은 유지/);
  assert.equal(await controller.save(), false); assert.equal(puts, 1);
  controller.acceptLatest(); assert.equal(controller.getState().editing.revision, 2); assert.equal(controller.getState().draft.description, "저장할 초안"); assert.equal(controller.getState().conflict, false);
  controller.dispose();
});

test("in-use targets permit description changes but prevent address changes and delete", async () => {
  const item = target({ in_use_profiles: [{ id: "synthetic-profile", name: "synthetic-draft", status: "draft" }] });
  const { controller, calls } = harness({ internalEgress: async () => [item] });
  await controller.refresh(); controller.edit(item); controller.update("description", "사용 중 설명 변경");
  assert.equal(await controller.save(), true); assert.equal(calls.length, 1);
  controller.edit(item); controller.update("port", "8001"); assert.equal(await controller.save(), false); assert.equal(await controller.remove(item), false); assert.equal(calls.length, 1);
  controller.restoreTarget(); assert.equal(controller.getState().draft.port, "8000");
  controller.dispose();
});

test("stale delete refreshes the list and requires another explicit delete action", async () => {
  let deletes = 0; let revision = 1;
  const { controller } = harness({ internalEgress: async () => [target({ revision })], deleteInternalEgress: async () => { deletes += 1; revision = 2; throw failure("internal_egress_changed", 409); } });
  await controller.refresh(); assert.equal(await controller.remove(controller.getState().items[0]), false);
  assert.equal(deletes, 1); assert.equal(controller.getState().items[0].revision, 2); assert.match(controller.getState().error, /다른 관리자/);
  controller.dispose();
});

test("repeated delete and form submissions cannot start another mutation while delete is pending", async () => {
  const response = deferred(); let deletes = 0;
  const { controller } = harness({ deleteInternalEgress: async () => { deletes += 1; return response.promise; } });
  await controller.refresh(); controller.edit(target());
  const deleting = controller.remove(target());
  assert.equal(await controller.remove(target()), false); assert.equal(await controller.save(), false); assert.equal(deletes, 1);
  response.resolve(); assert.equal(await deleting, true); assert.equal(controller.getState().editing, null);
  controller.dispose();
});

test("failed lookup never becomes an empty allowed list and a successful retry clears the lookup error", async () => {
  let broken = true;
  const { controller, calls } = harness({ internalEgress: async () => { if (broken) throw failure("unavailable", 503); return []; } });
  assert.equal(await controller.refresh(), false); assert.equal(controller.getState().items, null); assert.equal(controller.getState().needsRefresh, true);
  controller.update("ip_address", "10.0.0.1"); assert.equal(await controller.save(), false); assert.deepEqual(calls, []);
  broken = false; assert.equal(await controller.refresh(), true); assert.deepEqual(controller.getState().items, []); assert.equal(controller.getState().error, ""); assert.equal(controller.getState().needsRefresh, false);
  controller.dispose();
});

test("conflict followed by failed refresh keeps the draft and blocks edits until current state can be checked", async () => {
  let reads = 0;
  const { controller } = harness({ internalEgress: async () => { reads += 1; if (reads === 2) throw failure("down", 503); return [target({ revision: reads === 1 ? 1 : 2 })]; }, updateInternalEgress: async () => { throw failure("internal_egress_changed", 409); } });
  await controller.refresh(); controller.edit(target()); controller.update("description", "반드시 유지"); await controller.save();
  assert.equal(controller.getState().needsRefresh, true); controller.acceptLatest(); assert.equal(controller.getState().editing.revision, 1); assert.equal(controller.getState().draft.description, "반드시 유지");
  await controller.refresh(); assert.equal(controller.getState().conflict, true); controller.acceptLatest(); assert.equal(controller.getState().editing.revision, 2); assert.equal(controller.getState().draft.description, "반드시 유지");
  controller.dispose();
});

test("deleted edit target remains recoverable as an unsaved draft, rather than being silently recreated", async () => {
  let reads = 0; const { controller, calls } = harness({ internalEgress: async () => ++reads === 1 ? [target()] : [], updateInternalEgress: async () => { throw failure("internal_egress_not_found", 404); } });
  await controller.refresh(); controller.edit(target()); controller.update("description", "사라진 대상 초안"); await controller.save();
  assert.equal(controller.getState().draft.description, "사라진 대상 초안"); assert.equal(controller.getState().conflict, true); controller.acceptLatest(); assert.equal(await controller.save(), false); assert.deepEqual(calls, []);
  controller.dispose();
});

test("out-of-order reads and late responses after unmount cannot republish stale target state", async () => {
  const first = deferred(), second = deferred(); let reads = 0; const updates = [];
  const controller = createInternalEgressController({ api: { internalEgress: () => ++reads === 1 ? first.promise : second.promise }, onChange: state => updates.push(state) });
  const one = controller.refresh(); const two = controller.refresh(); second.resolve([target({ revision: 2 })]); await two; first.resolve([target({ revision: 1 })]); await one;
  assert.equal(controller.getState().items[0].revision, 2);
  controller.dispose(); const count = updates.length; await controller.refresh(); assert.equal(updates.length, count);
  const pending = deferred(); const other = createInternalEgressController({ api: { internalEgress: () => pending.promise }, onChange: state => updates.push(state) });
  const waiting = other.refresh(); other.dispose(); const disposedCount = updates.length; pending.resolve([]); await waiting; assert.equal(updates.length, disposedCount);
});

const bundled = buildSync({ entryPoints: [fileURLToPath(new URL("./InternalEgressSettings.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
const module = { exports: {} };
runInNewContext(bundled, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL });
const { InternalEgressView } = module.exports;
const markup = state => renderToStaticMarkup(createElement(InternalEgressView, { state, controller: {} }));

test("settings render usage locks, private range guidance, missing data and errors without claiming connection validation", () => {
  const item = target({ description: '<script>SYNTHETIC</script>', in_use_profiles: [{ id: "p", name: "synthetic-draft", status: "draft" }] });
  const html = markup({ ...emptyInternalEgressState(), items: [item], editing: item, draft: { ip_address: item.ip_address, port: "8000", description: item.description } });
  assert.match(html, /<h2>내부 연결 허용<\/h2>/); assert.match(html, /설명만 수정 가능 · 주소 변경·삭제는 연결 프로필을 모두 비활성화한 뒤/); assert.doesNotMatch(html, /내부 연결 허용 설명|사용 중인 내부 대상 설명|revision|<form/);
  assert.match(html, /disabled="" aria-label="10.0.0.10:8000 삭제"/);
  const source = readFileSync(new URL("./InternalEgressSettings.jsx", import.meta.url), "utf8");
  assert.match(source, /RFC1918 IPv4와 ULA IPv6/); assert.match(html, /OpenAI 설정에는 영향이 없으며 방화벽 설정·연결 검증을 대신하지/); assert.match(source, /readOnly=\{inUse\}/); assert.match(source, /닫기 · 입력 유지/);
  assert.match(source, /<HelpTooltip label="내부 IP"/); assert.match(source, /주소 변경·삭제는 연결 프로필을 모두 비활성화한 뒤/);
  assert.match(html, /&lt;script&gt;SYNTHETIC&lt;\/script&gt;/); assert.doesNotMatch(html, /<script>/);
  const failed = markup({ ...emptyInternalEgressState(), error: "조회 실패", needsRefresh: true });
  assert.match(failed, /role="alert">조회 실패/); assert.doesNotMatch(failed, /등록된 내부 대상이 없습니다/);
  const empty = markup({ ...emptyInternalEgressState(), items: [] }); assert.match(empty, /등록된 내부 대상이 없습니다/);
});

test("egress API methods preserve route, method, revision, credential and abort contracts", { concurrency: false }, async () => {
  const original = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options) => { calls.push([url, options]); return new Response(options.method === "DELETE" ? null : JSON.stringify([]), { status: options.method === "DELETE" ? 204 : 200, headers: { "Content-Type": "application/json" } }); };
  try {
    const abort = new AbortController(); await api.internalEgress({ signal: abort.signal });
    await api.createInternalEgress({ ip_address: "10.0.0.1", port: 8000, description: "합성" });
    await api.updateInternalEgress("synthetic/id", { ip_address: "10.0.0.1", port: 8000, description: "합성", expected_revision: 3 });
    assert.equal(await api.deleteInternalEgress("synthetic/id", 4), null);
    assert.equal(calls[0][0], "/api/v1/admin/internal-egress"); assert.equal(calls[0][1].signal, abort.signal);
    assert.equal(calls[1][1].method, "POST"); assert.equal(calls[2][1].method, "PUT"); assert.equal(JSON.parse(calls[2][1].body).expected_revision, 3);
    assert.equal(calls[2][0], "/api/v1/admin/internal-egress/synthetic%2Fid"); assert.equal(calls[3][0], "/api/v1/admin/internal-egress/synthetic%2Fid?expected_revision=4"); assert.equal(calls[3][1].method, "DELETE");
    assert.ok(calls.every(([, options]) => options.credentials === "include"));
  } finally { globalThis.fetch = original; }
});
