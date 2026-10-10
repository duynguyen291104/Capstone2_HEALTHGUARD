import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { test } from "node:test";
import { loadTs } from "./load-ts.mjs";

const { RequestSequence, isCalendarDate, safeNextPath, validatePasswordChange, validateScheduleValues, createRequestKey, responseActorName } = loadTs("lib/workflow.ts");

test("latest page response wins; logout invalidates an earlier auth refresh", () => {
  const sequence = new RequestSequence();
  const oldDay = sequence.start();
  const newDay = sequence.start();
  assert.equal(sequence.isCurrent(oldDay), false);
  assert.equal(sequence.isCurrent(newDay), true);
  sequence.invalidate();
  assert.equal(sequence.isCurrent(newDay), false);
});

test("empty/invalid calendar dates are rejected before formatting or upload", () => {
  assert.equal(isCalendarDate(""), false);
  assert.equal(isCalendarDate("2026-02-31"), false);
  assert.equal(isCalendarDate("2026-02-29"), false);
  assert.equal(isCalendarDate("2028-02-29"), true);
  assert.equal(isCalendarDate("2026-10-10"), true);
});

test("login return path remains internal and retains invitation query", () => {
  assert.equal(safeNextPath("/tham-gia?token=demo#review"), "/tham-gia?token=demo#review");
  for (const value of ["https://elsewhere.invalid", "//elsewhere.invalid", "/\\elsewhere.invalid", "/\nelsewhere.invalid"]) assert.equal(safeNextPath(value), "");
});

test("password rules do not rely on browser HTML validation", () => {
  assert.match(validatePasswordChange("current-password", "short", "short"), /10 đến 128/);
  assert.match(validatePasswordChange("current-password", "changed-password", "different-password"), /khớp/);
  assert.match(validatePasswordChange("current-password", "current-password", "current-password"), /khác/);
  assert.equal(validatePasswordChange("current-password", "changed-password", "changed-password"), null);
});

test("manual schedule blocks invalid dose/time/dates before a write", () => {
  const valid = { name: "Fictional", amount: "0.5", unit: "viên", time: "08:00", days: [0], startDate: "2026-10-10", endDate: "2026-10-16", instructions: "Demo" };
  assert.equal(validateScheduleValues(valid), null);
  for (const amount of ["0", "-1", "0.001", "Infinity", "1000000"]) assert.match(validateScheduleValues({ ...valid, amount }), /Liều/);
  assert.match(validateScheduleValues({ ...valid, time: "25:00" }), /giờ/);
  assert.match(validateScheduleValues({ ...valid, days: [] }), /ngày dùng/);
  assert.match(validateScheduleValues({ ...valid, endDate: "2026-10-09" }), /Ngày kết thúc/);
  assert.match(validateScheduleValues({ ...valid, startDate: "2026-02-31" }), /bắt đầu/);
});

test("idempotency UUID works on local HTTP without crypto.randomUUID", () => {
  assert.match(createRequestKey(), /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  const fallback = loadTs("lib/workflow.ts", { crypto: { getRandomValues: webcrypto.getRandomValues.bind(webcrypto) } }).createRequestKey;
  const keys = new Set(Array.from({ length: 10 }, () => fallback()));
  assert.equal(keys.size, 10);
  for (const key of keys) assert.match(key, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

test("response author prefers API name and never substitutes an unrelated current user", () => {
  const current = { id: "owner-id", full_name: "Owner demo" };
  assert.equal(responseActorName({ responded_by_user_id: "caregiver-id", responded_by_name: " Caregiver demo " }, current), "Caregiver demo");
  assert.equal(responseActorName({ responded_by_user_id: "owner-id" }, current), "Owner demo");
  assert.equal(responseActorName({ responded_by_user_id: "caregiver-id", responded_by_name: null }, current), "Tài khoản caregive");
});
