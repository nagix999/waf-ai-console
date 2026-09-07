import assert from "node:assert/strict";
import { analysisQuery, dashboardListState, dateValue, emptyFilters, executionDuration, formatDuration, initialListState, uploadErrorText, validateFilters } from "./analysisView.js";

const filters = { ...emptyFilters, analysis_purpose: "test", q: "  192.0.2.10  ", search_field: "src_ip", input_truncated: "false", confidence_min: "0", src_port: "0", created_from: "2026-09-05T09:00:00+09:00", created_to: "2026-09-06T09:00:00+09:00" };
assert.deepEqual(analysisQuery(filters, 25, 50), {
  limit: 25, offset: 50, analysis_purpose: "test", q: "192.0.2.10", search_field: "src_ip", input_truncated: "false", confidence_min: "0", src_port: "0", created_from: "2026-09-05T00:00:00.000Z", created_to: "2026-09-06T00:00:00.000Z"
});
assert.deepEqual(analysisQuery({ ...emptyFilters, q: "   " }, 50, 0), { limit: 50, offset: 0 });
assert.deepEqual(analysisQuery({ ...emptyFilters, input_truncated: false, confidence_min: 0 }, 25, 0), { limit: 25, offset: 0, input_truncated: false, confidence_min: 0 });
assert.equal(validateFilters({ ...emptyFilters, confidence_min: "0.9", confidence_max: "0.1" }), "신뢰도 최솟값은 최댓값보다 클 수 없습니다.");
assert.equal(validateFilters({ ...emptyFilters, confidence_min: "0", confidence_max: "0" }), "");
assert.notEqual(validateFilters({ ...emptyFilters, created_from: "2026-09-05T12:00", created_to: "2026-09-05T12:00" }), "");
assert.equal(formatDuration(null), "측정 전 데이터");
assert.equal(formatDuration(undefined), "측정 전 데이터");
assert.equal(formatDuration(0), "0 ms");
assert.equal(formatDuration(13500), "13.50초");
assert.equal(formatDuration(61500), "1분 1초");
assert.equal(formatDuration(3661000), "1시간 1분 1초");
assert.equal(dateValue("2026-09-05T00:00:00").toISOString(), "2026-09-05T00:00:00.000Z");
assert.equal(dateValue("2026-09-05T09:00:00+09:00").toISOString(), "2026-09-05T00:00:00.000Z");
assert.equal(executionDuration({ status: "completed", duration_ms: null, started_at: "2026-09-05T00:00:00Z", completed_at: "2026-09-05T00:00:00Z" }), "측정 전 데이터");
assert.equal(executionDuration({ status: "failed", duration_ms: 1250 }), "1.25초");
assert.equal(executionDuration({ status: "running", duration_ms: 1250 }), "1.25초 · 진행 중");
assert.equal(executionDuration({ status: "failed", duration_ms: null, metadata: { timing_incomplete: true } }), "측정 중단");
assert.equal(executionDuration({ status: "running", duration_ms: null, started_at: "2026-09-05T00:00:00Z", completed_at: null }, Date.parse("2026-09-05T00:00:02Z")), "2.00초 · 진행 중");
assert.equal(executionDuration({ status: "running", started_at: "2026-09-05T00:00:02Z" }, Date.parse("2026-09-05T00:00:00Z")), "0 ms · 진행 중");
const state = initialListState("test");
state.draft.q = "edited";
assert.equal(state.applied.q, "");
assert.equal(state.applied.analysis_purpose, "test");
assert.equal(initialListState().draft.q, "");
console.log("Analysis query, validation, UTC dates, duration and state tests passed.");

const dashboardState = dashboardListState({ verdict: "false_positive", status: "completed", waf_action: "D", created_from: "2026-09-05T00:00:00.123Z", created_to: "2026-09-06T00:00:00.456Z" });
assert.equal(dashboardState.applied.analysis_purpose, "production");
assert.equal(dashboardState.advanced, true);
assert.equal(dashboardState.offset, 0);
assert.equal(analysisQuery(dashboardState.draft, 25, 0).created_from, dashboardState.applied.created_from);
assert.equal(analysisQuery(dashboardState.draft, 25, 0).created_to, dashboardState.applied.created_to);
assert.equal(analysisQuery(dashboardState.applied, 25, 0).waf_action, "D");
assert.equal(uploadErrorText({ message: "event_id_conflict" }), "event_id_conflict");
assert.equal(uploadErrorText({ validation: [{ field: "src_ip", type: "ip_any_address" }, { field: "payload", type: "missing" }] }), "src_ip: ip_any_address\npayload: missing");
assert.equal(uploadErrorText({}), "validation_failed");
console.log("Dashboard drilldown boundaries and safe upload errors passed.");
