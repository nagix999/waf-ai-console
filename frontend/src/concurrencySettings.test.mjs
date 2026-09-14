import assert from "node:assert/strict";
import test from "node:test";
import { concurrencyDraft, concurrencyIssue, createConcurrencyController, validConcurrencyCatalog } from "./concurrencySettings.js";

const catalog = () => ({ state_token: "a".repeat(64), revision: 0, production: 1, test: 1,
  servers: [{ server_key: "10.0.0.10:8000", max_calls: 1, profiles: [{ id: "fixture", name: "내부 모델" }] }],
  active: { production: 0, test: 0 } });

test("strict bounds, empty input and shared-server draft", () => {
  assert.ok(validConcurrencyCatalog(catalog()));
  assert.equal(concurrencyIssue(concurrencyDraft(catalog())), "");
  for (const production of ["", "10", true, 0, 33, 1.5, NaN]) {
    assert.ok(concurrencyIssue({ ...concurrencyDraft(catalog()), production }));
    assert.equal(validConcurrencyCatalog({ ...catalog(), production }), false);
  }
  assert.ok(concurrencyIssue({ production: 10, test: 10, servers: [{ max_calls: 65 }] }));
  const duplicate = catalog(); duplicate.servers.push(duplicate.servers[0]);
  assert.equal(validConcurrencyCatalog(duplicate), false);
});

test("save submits counts and endpoint limits once without model assignment changes", async () => {
  const writes = [];
  const controller = createConcurrencyController({ api: { concurrencySettings: async () => catalog(),
    updateConcurrencySettings: async payload => { writes.push(payload); return { ...catalog(), ...payload, servers: [{ ...catalog().servers[0], ...payload.servers[0] }] }; } }, onChange() {} });
  await controller.refresh(); controller.change("production", 10); controller.change("server", 10, "10.0.0.10:8000");
  assert.equal(await controller.save(), true);
  assert.deepEqual(writes, [{ expected_state_token: "a".repeat(64), production: 10, test: 1, servers: [{ server_key: "10.0.0.10:8000", max_calls: 10 }] }]);
  assert.equal(controller.getState().draft.production, 10);
});

test("ambiguous or stale save blocks automatic retries and edits until refreshed", async () => {
  for (const message of ["network", "concurrency_configuration_changed"]) {
    let writes = 0;
    const controller = createConcurrencyController({ api: { concurrencySettings: async () => catalog(),
      updateConcurrencySettings: async () => { writes++; throw new Error(message); } }, onChange() {} });
    await controller.refresh(); controller.change("test", 3);
    assert.equal(await controller.save(), false); assert.equal(await controller.save(), false);
    controller.change("test", 5);
    assert.equal(writes, 1); assert.equal(controller.getState().draft.test, 3);
    assert.ok(controller.getState().needsRefresh);
    await controller.refresh(); assert.equal(controller.getState().needsRefresh, false);
  }
});

test("malformed data fails closed; late response after unmount cannot write", async () => {
  const invalid = createConcurrencyController({ api: { concurrencySettings: async () => ({}) }, onChange() {} });
  await invalid.refresh(); assert.ok(invalid.getState().needsRefresh); assert.equal(await invalid.save(), false);
  let resolve, updates = 0;
  const controller = createConcurrencyController({ api: { concurrencySettings: () => new Promise(done => { resolve = done; }) }, onChange() { updates++; } });
  const pending = controller.refresh(); controller.dispose(); resolve(catalog()); await pending;
  assert.equal(updates, 1); assert.equal(await controller.save(), false);
});

test("pending save disables duplicate submit and retains the confirmed values", async () => {
  let resolve, writes = 0;
  const controller = createConcurrencyController({ api: { concurrencySettings: async () => catalog(),
    updateConcurrencySettings: () => { writes++; return new Promise(done => { resolve = done; }); } }, onChange() {} });
  await controller.refresh(); controller.change("test", 4);
  const pending = controller.save(); controller.change("test", 9);
  assert.equal(await controller.save(), false); assert.equal(controller.getState().draft.test, 4);
  resolve({ ...catalog(), test: 4 }); assert.equal(await pending, true); assert.equal(writes, 1);
});
