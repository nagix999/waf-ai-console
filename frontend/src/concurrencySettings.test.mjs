import assert from "node:assert/strict";
import test from "node:test";
import { concurrencyDraft, concurrencyIssue, createConcurrencyController, MAX_ANALYSIS_CONCURRENCY, MAX_SERVER_CALLS, validConcurrencyCatalog } from "./concurrencySettings.js";

const catalog = () => ({ state_token: "a".repeat(64), revision: 0, production: 1, test: 1,
  servers: [{ server_key: "10.0.0.10:8000", max_calls: 1, profiles: [{ id: "fixture", name: "내부 모델" }] }],
  active: { production: 0, test: 0 } });

test("strict bounds, empty input and shared-server draft", () => {
  assert.ok(validConcurrencyCatalog(catalog()));
  assert.equal(concurrencyIssue(concurrencyDraft(catalog())), "");
  assert.equal(MAX_ANALYSIS_CONCURRENCY, 512);
  assert.equal(MAX_SERVER_CALLS, 64);
  for (const field of ["production", "test"]) {
    for (const value of [1, 32, 33, 511, 512]) {
      assert.equal(concurrencyIssue({ ...concurrencyDraft(catalog()), [field]: value }), "");
      assert.ok(validConcurrencyCatalog({ ...catalog(), [field]: value }));
    }
    for (const value of ["", "10", "512", true, false, null, undefined, 0, -1, 513, 1.5, 511.5, NaN, Infinity]) {
      assert.match(concurrencyIssue({ ...concurrencyDraft(catalog()), [field]: value }), /1~512/);
      assert.equal(validConcurrencyCatalog({ ...catalog(), [field]: value }), false);
    }
  }
  for (const max_calls of [1, 64]) {
    assert.equal(concurrencyIssue({ production: 512, test: 512, servers: [{ max_calls }] }), "");
  }
  for (const max_calls of [0, 65, 512, "64", true, 1.5]) {
    assert.match(concurrencyIssue({ production: 512, test: 512, servers: [{ max_calls }] }), /1~64/);
    const invalid = catalog(); invalid.servers[0].max_calls = max_calls;
    assert.equal(validConcurrencyCatalog(invalid), false);
  }
  const duplicate = catalog(); duplicate.servers.push(duplicate.servers[0]);
  assert.equal(validConcurrencyCatalog(duplicate), false);
});

test("loading preserves existing counts and accepts saved 512 without automatic increases", async () => {
  for (const counts of [{ production: 1, test: 1 }, { production: 7, test: 32 }, { production: 512, test: 512 }]) {
    const value = { ...catalog(), ...counts };
    const controller = createConcurrencyController({ api: { concurrencySettings: async () => value }, onChange() {} });
    await controller.refresh();
    assert.equal(controller.getState().needsRefresh, false);
    assert.equal(controller.getState().loading, false);
    assert.deepEqual(controller.getState().draft, { ...counts, servers: [{ server_key: "10.0.0.10:8000", max_calls: 1 }] });
    assert.equal(value.production, counts.production); assert.equal(value.test, counts.test);
  }
});

test("512 is accepted by draft validation, save and returned catalog with separate server limit", async () => {
  const writes = [];
  const controller = createConcurrencyController({ api: { concurrencySettings: async () => catalog(),
    updateConcurrencySettings: async payload => {
      writes.push(payload);
      return { ...catalog(), production: payload.production, test: payload.test,
        servers: [{ ...catalog().servers[0], max_calls: payload.servers[0].max_calls }] };
    } }, onChange() {} });
  await controller.refresh();
  controller.change("production", 512); controller.change("test", 512);
  controller.change("server", 64, "10.0.0.10:8000");
  assert.equal(await controller.save(), true);
  assert.deepEqual(writes, [{ expected_state_token: "a".repeat(64), production: 512, test: 512,
    servers: [{ server_key: "10.0.0.10:8000", max_calls: 64 }] }]);
  assert.equal(controller.getState().draft.production, 512);
  assert.equal(controller.getState().draft.test, 512);
  assert.equal(controller.getState().draft.servers[0].max_calls, 64);
  assert.equal(controller.getState().needsRefresh, false);
});

test("invalid analysis values cannot be saved and invalid returned bounds require refresh", async () => {
  let writes = 0;
  const controller = createConcurrencyController({ api: { concurrencySettings: async () => catalog(),
    updateConcurrencySettings: async () => { writes++; return { ...catalog(), test: 513 }; } }, onChange() {} });
  await controller.refresh();
  for (const field of ["production", "test"]) {
    for (const value of [0, 513, true, "512", 511.5]) {
      controller.change(field, value);
      assert.equal(await controller.save(), false);
      assert.equal(writes, 0);
    }
    controller.change(field, 512);
  }
  assert.equal(await controller.save(), false);
  assert.equal(writes, 1);
  assert.equal(controller.getState().needsRefresh, true);
  assert.equal(await controller.save(), false);
  assert.equal(writes, 1);
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
