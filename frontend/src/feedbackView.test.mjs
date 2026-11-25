import assert from "node:assert/strict";
import test from "node:test";
import { analysisFeedbackState, feedbackCategoriesOf, feedbackCategoryLabel, feedbackCount, feedbackDateIssue, feedbackDateTime, feedbackQuery,
  feedbackUrlState, feedbackVersionLabel, initialFeedbackState, normalizeFeedbackState, recentFeedbackRange, validFeedbackDate, validFeedbackSummary } from "./feedbackView.js";

const id = "10000000-0000-4000-8000-000000000001";

test("feedback periods use the KST calendar and include both boundary dates", () => {
  assert.deepEqual(recentFeedbackRange(7, "2026-11-25T14:59:59Z"), { start: "2026-11-19", end: "2026-11-25" });
  assert.deepEqual(recentFeedbackRange(7, "2026-11-25T15:00:00Z"), { start: "2026-11-20", end: "2026-11-26" });
  assert.deepEqual(recentFeedbackRange(30, "2024-03-01T00:00:00Z"), { start: "2024-02-01", end: "2024-03-01" });
  assert.equal(initialFeedbackState("2026-11-25T00:00:00Z").purpose, "production");
});

test("per-analysis feedback includes older history and keeps purpose and identifiers scoped", () => {
  const state = analysisFeedbackState({ id, analysis_purpose: "test", created_at: "2026-01-01T16:00:00Z", payload: "PRIVATE", event_id: "PRIVATE" }, "2026-11-25T00:00:00Z");
  assert.equal(state.start, "2026-01-02");
  assert.equal(state.end, "2026-11-25");
  assert.equal(state.purpose, "test");
  assert.equal(state.analysis, id);
  assert.equal(state.version, "");
  assert.ok(!JSON.stringify(state).includes("PRIVATE"));
  assert.equal(analysisFeedbackState({ id, analysis_purpose: "legacy_unknown" }, "2026-11-25T00:00:00Z").purpose, "all");
  for (const created_at of ["invalid", null, "2027-01-01T00:00:00Z"]) {
    assert.equal(analysisFeedbackState({ id, created_at }, "2026-11-25T00:00:00Z").start, "2026-10-27");
  }
});

test("invalid calendar dates and reversed periods cannot issue a feedback query", () => {
  assert.equal(validFeedbackDate("2024-02-29"), true);
  for (const date of ["2026-02-29", "2026-04-31", "2026-1-01", "2026-11-25T00:00:00Z", "0000-01-01", "", null]) assert.equal(validFeedbackDate(date), false);
  assert.equal(feedbackDateIssue({ start: "2026-11-26", end: "2026-11-25" }), "date_order");
  assert.equal(feedbackQuery({ start: "2026-02-29", end: "2026-03-01" }), null);
  assert.equal(feedbackQuery({ start: "2026-11-26", end: "2026-11-25" }), null);
});

test("queries paginate on the server and use registration-date and analysis-time version fields", () => {
  const state = { start: "2026-11-25", end: "2026-11-25", purpose: "all", version: id, category: "other", analysis: id, query: " sample ", offset: 40, limit: 20 };
  assert.deepEqual(feedbackQuery(state, true), { date_from: "2026-11-25", date_to: "2026-11-25", analysis_purpose: "production", production_configuration_id: id, category: "other", analysis_id: id, q: "sample", page: 3, page_size: 20 });
  assert.equal(feedbackQuery(state).page, undefined);
  const testQuery = feedbackQuery({ ...state, purpose: "test" });
  assert.equal(testQuery.analysis_purpose, "test");
  assert.equal(testQuery.production_configuration_id, undefined);
  assert.equal(feedbackQuery({ ...state, version: "__unrecorded__" }).production_configuration_id, "__unrecorded__");
});

test("URL state excludes searches and arbitrary data while preserving safe scope and selected feedback", () => {
  const state = feedbackUrlState({ start: "2026-11-01", end: "2026-11-25", purpose: "all", category: "other", selectedId: id, query: "private event metadata", comment: "private comment", version: "invalid" });
  assert.equal(state.query, undefined);
  assert.equal(state.comment, undefined);
  assert.equal(state.selectedId, id);
  assert.equal(state.version, "");
  const invalidDates = feedbackUrlState({ start: "2026-02-29", end: "private text" });
  assert.equal(invalidDates.start, undefined);
  assert.equal(invalidDates.end, undefined);
  assert.equal(normalizeFeedbackState({ analysis: "../../admin", category: "constructor", limit: 10000, offset: -1 }).analysis, "");
  assert.equal(normalizeFeedbackState({ query: "a".repeat(250) }).query.length, 200);
});

test("unrecorded versions and unknown values are displayed without inventing attribution", () => {
  assert.equal(feedbackVersionLabel({ analysis_purpose: "test", production_configuration_id: id }), "Test / 적용 안 함");
  assert.equal(feedbackVersionLabel({ analysis_purpose: "production", production_configuration_id: null }), "버전 미기록");
  assert.equal(feedbackVersionLabel({ id, analysis_purpose: "production", production_configuration_id: null }), "버전 미기록");
  assert.equal(feedbackVersionLabel({ analysis_purpose: "production", production_configuration_id: id }), "등록 정보가 없는 버전");
  assert.equal(feedbackVersionLabel({ id, label: "운영 구성 2" }), "운영 구성 2");
  assert.equal(feedbackVersionLabel({ analysis_purpose: "legacy_unknown" }, "en"), "Purpose not recorded");
  assert.equal(feedbackDateTime("2026-11-25 12:00:00"), "미기록");
  assert.equal(feedbackDateTime("2026-02-30T00:00:00Z"), "미기록");
  assert.match(feedbackDateTime("2026-11-25T15:01:00Z", "en"), /26\/11\/2026, 00:01/);
  assert.deepEqual(feedbackCategoriesOf({ feedback_categories: ["other", "other", "unsupported"] }), ["other"]);
  assert.equal(feedbackCategoryLabel("constructor", "en"), "Unrecognized category");
});

test("missing or malformed server metrics are distinct from a measured zero", () => {
  assert.equal(feedbackCount(null), null);
  assert.equal(feedbackCount(0), 0);
  assert.equal(feedbackCount(-1), null);
  const summary = { registration_count: 0, analysis_count: 0, multi_category_count: 0, comment_only_count: 0,
    category_counts: ["insufficient_evidence", "evidence_mismatch", "explanation_error", "exaggerated_impact", "inappropriate_severity", "other"].map(category => ({ category, count: 0 })) };
  assert.equal(validFeedbackSummary(summary), true);
  assert.equal(validFeedbackSummary({ ...summary, category_counts: [] }), false);
  assert.equal(validFeedbackSummary({ ...summary, analysis_count: null }), false);
});
