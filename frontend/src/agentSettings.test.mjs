import assert from "node:assert/strict";
import test from "node:test";
import { createAgentSettingsController, diagnosticLabels, hasExternalRole, selectionIssue, validAgentCatalog } from "./agentSettings.js";
import { readAppHash } from "./appRoutes.js";
import { roleAssigned } from "./modelAssignments.js";

const catalog = () => ({ state_token: "a".repeat(64), revision: 0,
  assignments: { production: { primary_profile_id: "local", verifier_profile_id: null }, test: { primary_profile_id: null, verifier_profile_id: null } },
  profiles: [{ id: "local", can_assign: true, provider: "vllm" }, { id: "remote", can_assign: true, provider: "openai" }, { id: "draft", can_assign: false }] });

test("input integrity limit is distinct from model disagreement", () => {
  assert.equal(diagnosticLabels.input_integrity_limited, "근거 구간의 누락·구조 문제");
  assert.notEqual(diagnosticLabels.input_integrity_limited, diagnosticLabels.verdict_disagreement);
});

test("validated role selection and explicit external transfer", () => {
  const value = catalog(); const draft = structuredClone(value.assignments);
  assert.ok(validAgentCatalog(value)); assert.equal(selectionIssue(value, draft), "");
  assert.equal(hasExternalRole(value, draft), false);
  draft.production.verifier_profile_id = "remote";
  assert.equal(hasExternalRole(value, draft), false); // Production is read-only here.
  draft.test.primary_profile_id = "remote";
  assert.equal(hasExternalRole(value, draft), true);
  draft.test.primary_profile_id = null;
  draft.test.verifier_profile_id = "local";
  assert.match(selectionIssue(value, draft), /Primary/);
  draft.test.primary_profile_id = "draft";
  assert.match(selectionIssue(value, draft), /전체 검증/);
  assert.ok(roleAssigned({ status: "verified", agent_roles: ["production.verifier"] }));
});

test("old prompt bookmark opens integrated Agent settings", () => {
  assert.deepEqual(readAppHash("#settings/prompts"), { page: "settings", tab: "instructions" });
  assert.deepEqual(readAppHash("#settings/agents"), { page: "settings", tab: "agents" });
});

test("save is one atomic request, no LLM call and no unacknowledged external assignment", async () => {
  let writes = [];
  const value = catalog();
  const controller = createAgentSettingsController({ api: { agentSettings: async () => value,
    updateAgentSettings: async payload => { writes.push(payload); return { ...value, assignments: { production: value.assignments.production, test: payload.test } }; } }, onChange() {} });
  await controller.refresh(); controller.change("production", "verifier_profile_id", "remote");
  assert.equal(controller.getState().draft.production.verifier_profile_id, null);
  controller.change("test", "primary_profile_id", "remote");
  assert.equal(await controller.save(false), false); assert.equal(writes.length, 0);
  assert.equal(await controller.save(true), true); assert.equal(writes.length, 1);
  assert.equal(writes[0].expected_state_token, "a".repeat(64));
  assert.equal(writes[0].test.primary_profile_id, "remote");
  assert.equal(writes[0].production, undefined);
});

test("ambiguous write failure blocks resubmission until explicit refresh", async () => {
  let writes = 0;
  const controller = createAgentSettingsController({ api: { agentSettings: async () => catalog(),
    updateAgentSettings: async () => { writes++; throw new Error("network"); } }, onChange() {} });
  await controller.refresh(); controller.change("production", "verifier_profile_id", "local");
  await controller.save(false); await controller.save(false);
  assert.equal(writes, 1); assert.ok(controller.getState().needsRefresh);
  await controller.refresh(); assert.equal(controller.getState().needsRefresh, false);
});

test("disposed controller ignores late reads and does not save", async () => {
  let resolve; let updates = 0;
  const controller = createAgentSettingsController({ api: { agentSettings: () => new Promise(done => { resolve = done; }) }, onChange() { updates++; } });
  const pending = controller.refresh(); controller.dispose(); resolve(catalog()); await pending;
  assert.equal(updates, 1); assert.equal(await controller.save(true), false);
});
