import assert from "node:assert/strict";
import { test } from "node:test";
import { loadTs } from "./load-ts.mjs";

function apiHarness(fetchImpl, storage = null) {
  const events = [];
  const window = {
    localStorage: storage ?? { getItem: () => "demo-group", setItem() {}, removeItem() {} },
    dispatchEvent(event) { events.push(event.type); return true; },
  };
  const api = loadTs("lib/api.ts", { window, fetch: fetchImpl, Headers, Response, Blob, Event, TypeError, setTimeout, process: { env: {} } });
  return { api, events };
}

function failure(status, code) {
  return new Response(JSON.stringify({ error: { code, message: "Demo error" } }), { status, headers: { "Content-Type": "application/json" } });
}

test("expired session updates auth state; wrong login credentials do not sign out another session", async () => {
  const { api, events } = apiHarness(async () => failure(401, "INVALID_SESSION"));
  await assert.rejects(api.eldersApi.list(), error => error.status === 401);
  assert.deepEqual(events, [api.SESSION_EXPIRED_EVENT]);
  const invalidLogin = apiHarness(async () => failure(401, "INVALID_CREDENTIALS"));
  await assert.rejects(invalidLogin.api.authApi.login({ email: "demo@example.com", password: "incorrect" }));
  assert.deepEqual(invalidLogin.events, []);
});

test("revoked membership triggers revalidation without declaring the session expired", async () => {
  const { api, events } = apiHarness(async () => failure(403, "GROUP_ACCESS_DENIED"));
  await assert.rejects(api.eldersApi.list());
  assert.deepEqual(events, [api.GROUP_ACCESS_CHANGED_EVENT]);
});

test("a late 401 from an old session cannot sign out a newly logged-in user", async () => {
  let complete;
  const { api, events } = apiHarness(() => new Promise(resolve => { complete = resolve; }));
  const oldRequest = api.eldersApi.list();
  api.markAuthChanged();
  complete(failure(401, "INVALID_SESSION"));
  await assert.rejects(oldRequest);
  assert.deepEqual(events, []);
});

test("network loss does not emit logout and medical requests bypass cache", async () => {
  let options;
  const { api, events } = apiHarness(async (_url, requestOptions) => { options = requestOptions; throw new Error("Offline"); });
  await assert.rejects(api.eldersApi.list(), error => error.code === "NETWORK_ERROR");
  assert.deepEqual(events, []);
  assert.equal(options.cache, "no-store");
  assert.equal(options.credentials, "include");
});

test("disabled browser storage does not prevent cookie login; stale group is not trusted", async () => {
  const result = { user: { id: "demo-user", full_name: "Demo", email: "demo@example.com" }, groups: [{ id: "current-group", name: "Demo group", role: "CAREGIVER" }], default_group_id: "current-group" };
  const storage = { getItem() { throw new Error("Blocked storage"); }, setItem() { throw new Error("Blocked storage"); }, removeItem() { throw new Error("Blocked storage"); } };
  const { api } = apiHarness(async () => new Response(JSON.stringify(result)), storage);
  const user = await api.authApi.me();
  assert.equal(user.current_group.care_group_id, "current-group");
  assert.doesNotThrow(() => api.storeGroupId("current-group"));
});

test("per-slot idempotency key is preserved on both initial creation and retry", async () => {
  const keys = [];
  const { api } = apiHarness(async (_url, options) => { keys.push(options.headers.get("Idempotency-Key")); return new Response(JSON.stringify({ id: "same-schedule" })); });
  const key = "d972bd60-681e-4ca5-bc2c-93c9f5bf7460";
  await api.medicationApi.create("demo-elder", { medication_name: "Fictional" }, key);
  await api.medicationApi.create("demo-elder", { medication_name: "Fictional" }, key);
  assert.deepEqual(keys, [key, key]);
});

test("a transient read retries once; HTTP failures and mutation writes never auto-retry", async () => {
  let reads = 0;
  const read = apiHarness(async () => { if (++reads === 1) throw new TypeError("Transient fetch failure"); return new Response("[]"); });
  assert.equal((await read.api.eldersApi.list()).length, 0);
  assert.equal(reads, 2);
  let httpCalls = 0;
  const http = apiHarness(async () => { httpCalls += 1; return failure(503, "UNAVAILABLE"); });
  await assert.rejects(http.api.eldersApi.list());
  assert.equal(httpCalls, 1);
  let writes = 0;
  const write = apiHarness(async () => { writes += 1; throw new TypeError("Lost write acknowledgement"); });
  await assert.rejects(write.api.medicationApi.create("demo", { medication_name: "Fictional" }));
  assert.equal(writes, 1);
});

test("read retry is bounded when the backend stays offline", async () => {
  let calls = 0;
  const offline = apiHarness(async () => { calls += 1; throw new TypeError("Offline"); });
  await assert.rejects(offline.api.eldersApi.list(), error => error.code === "NETWORK_ERROR");
  assert.equal(calls, 2);
});
