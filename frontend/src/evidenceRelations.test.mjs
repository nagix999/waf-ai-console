import assert from "node:assert/strict";
import test from "node:test";
import { evidenceRelationView } from "./evidenceRelations.js";
import { relationFixture, relationMapping, sharedRelationFixture } from "../scripts/evidence-relations-fixture.mjs";

test("source links preserve each role and classification without merging incompatible interpretations", () => {
  const result = relationFixture(), mapping = relationMapping(), before = JSON.stringify([result, mapping]);
  const view = evidenceRelationView(result, mapping);
  assert.equal(view.enabled, true);
  assert.equal(view.items.length, 4);
  assert.deepEqual(view.rows.map(item => [item.number, item.roles, item.supports]), [[2, ["primary"], "attack_request"], [4, ["verifier"], "normal_request"]]);
  assert.deepEqual(view.rows.map(item => item.related_sources[0].supports), ["context", "context"]);
  assert.equal(JSON.stringify([result, mapping]), before);
});

test("identical canonical group is displayed once with both original roles", () => {
  const { result, mapping } = sharedRelationFixture(), before = JSON.stringify([result, mapping]);
  const view = evidenceRelationView(result, mapping);
  assert.equal(view.enabled, true); assert.equal(view.rows.length, 1);
  assert.deepEqual(view.rows[0].roles, ["primary", "verifier"]);
  assert.equal(view.rows[0].number, 2);
  assert.deepEqual(view.rows[0].related_sources.map(item => [item.evidence_id, item.number, item.supports]), [["e1", 1, "context"]]);
  assert.equal(JSON.stringify([result, mapping]), before);
});

function addSharedContext(result) {
  result.evidence.push({ ...result.evidence[0], evidence_id: "e3", field: "payload.headers", excerpt: "Content-Type: application/json",
    interpretation_ko: "본문 전달 형식을 보여줍니다.",
    origins: ["primary", "verifier"].map(role => ({ role, index: 2, source_id: "c3" })),
  });
}

test("identical related-ID sets preserve the first group's original source order", () => {
  const { result, mapping } = sharedRelationFixture(); addSharedContext(result);
  mapping.groups[0].related_evidence_ids = ["e3", "e1"];
  mapping.groups[1].related_evidence_ids = ["e1", "e3"];
  const view = evidenceRelationView(result, mapping);
  assert.equal(view.rows.length, 1);
  assert.deepEqual(view.rows[0].related_sources.map(item => item.evidence_id), ["e3", "e1"]);
  assert.deepEqual(view.rows[0].roles, ["primary", "verifier"]);
});

test("partially different relations stay separate and leave unconsumed role sources visible", () => {
  const { result, mapping } = sharedRelationFixture(); addSharedContext(result);
  mapping.groups[0].related_evidence_ids = ["e1", "e3"];
  const view = evidenceRelationView(result, mapping), groups = view.rows.filter(item => item.relation);
  assert.equal(groups.length, 2);
  assert.deepEqual(groups.map(item => item.roles), [["primary"], ["verifier"]]);
  assert.ok(view.rows.some(item => !item.relation && item.evidence_id === "e3" && item.roles.join() === "verifier"));
});

test("different representative IDs are never merged even with identical explanation text", () => {
  const { result, mapping } = sharedRelationFixture();
  result.evidence.push({ ...result.evidence[1], evidence_id: "e3", source_spans: [{ start: 100, end: 126 }],
    origins: [{ role: "verifier", index: 2, source_id: "c3" }],
  });
  mapping.groups[1].representative_evidence_id = "e3";
  const groups = evidenceRelationView(result, mapping).rows.filter(item => item.relation);
  assert.equal(groups.length, 2);
  assert.deepEqual(groups.map(item => item.evidence_id), ["e2", "e3"]);
});

test("same excerpt with opposing interpretations remains separate across both role groups", () => {
  const view = evidenceRelationView(relationFixture(), relationMapping());
  assert.equal(view.rows.length, 2);
  assert.notEqual(view.rows[0].interpretation_ko, view.rows[1].interpretation_ko);
  assert.deepEqual(view.rows.map(item => item.supports), ["attack_request", "normal_request"]);
});

test("links do not consume the duplicate Editor mapping or change canonical evidence numbers", () => {
  const result = relationFixture();
  result.evidence_presentation = { version: "evidence-editor-v1", status: "completed", groups: [
    { member_ids: ["e1", "e3"], representative_id: "e3" }, { member_ids: ["e2"], representative_id: "e2" }, { member_ids: ["e4"], representative_id: "e4" },
  ] };
  const view = evidenceRelationView(result, relationMapping());
  assert.equal(view.enabled, true);
  assert.deepEqual(view.items.map(item => item.number), [1, 2, 3, 4]);
  assert.deepEqual(view.items.map(item => item.evidence_id), ["e1", "e2", "e3", "e4"]);
});

test("context representative never hides a directional source in collapsed context", () => {
  const mapping = relationMapping();
  mapping.groups = [{ role: "primary", representative_evidence_id: "e1", related_evidence_ids: ["e2"] }];
  const view = evidenceRelationView(relationFixture(), mapping);
  assert.ok(view.rows.some(item => item.evidence_id === "e2" && item.supports === "attack_request"));
});

test("shared context and nested representatives keep every representative explanation", () => {
  const result = relationFixture(), mapping = relationMapping();
  mapping.groups = [mapping.groups[0], { role: "primary", representative_evidence_id: "e1", related_evidence_ids: ["e2"] }];
  const view = evidenceRelationView(result, mapping);
  assert.equal(view.enabled, true);
  for (const id of ["e1", "e2"]) assert.ok(view.rows.some(item => item.evidence_id === id && item.roles.includes("primary")));
});

test("empty or missing links keep the previous Editor and legacy display unchanged", () => {
  for (const mapping of [undefined, { version: "evidence-relations-v1", groups: [] }]) {
    const view = evidenceRelationView(relationFixture(), mapping);
    assert.equal(view.enabled, false); assert.equal(view.invalid, false); assert.equal(view.rows, null);
  }
  assert.equal(evidenceRelationView({ evidence: [] }, relationMapping()).enabled, false);
});

const corruptions = [
  value => { value.version = "future"; }, value => { value.extra = true; },
  value => { value.groups[0].related_evidence_ids = []; }, value => { value.groups[0].related_evidence_ids = ["missing"]; },
  value => { value.groups[0].related_evidence_ids = ["e1", "e1"]; }, value => { value.groups[0].related_evidence_ids = ["e2"]; },
  value => { value.groups[0].role = "verifier"; }, value => { value.groups[0].related_evidence_ids = ["e4"]; },
  value => { value.groups.push(value.groups[0]); }, value => { value.groups[0].explanation = "not allowed"; },
];
for (const [index, corrupt] of corruptions.entries()) test(`invalid mapping ${index} falls back entirely`, () => {
  const mapping = relationMapping(); corrupt(mapping);
  const view = evidenceRelationView(relationFixture(), mapping);
  assert.equal(view.enabled, false); assert.equal(view.invalid, true); assert.equal(view.rows, null);
});

test("a role cannot group attack and normal interpretations even when both belong to it", () => {
  const result = relationFixture();
  result.evidence[3].origins.push({ role: "primary", index: 2, source_id: "c3" });
  const mapping = { version: "evidence-relations-v1", groups: [{ role: "primary", representative_evidence_id: "e2", related_evidence_ids: ["e4"] }] };
  assert.equal(evidenceRelationView(result, mapping).invalid, true);
});
