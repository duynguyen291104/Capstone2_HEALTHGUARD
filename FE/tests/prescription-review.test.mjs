import assert from "node:assert/strict";
import { test } from "node:test";
import { loadTs } from "./load-ts.mjs";

// Execute the actual TS helper, with type-only imports removed; no extra runner.
const { createReviewMedication, duplicateReviewSchedule, endDateForDuration, reviewFieldHint, validateReviewMedication, scheduleFromReview } = loadTs("lib/prescription-review.ts");

function source(values = {}) {
  return { medication_name: "Fictional medicine A", instructions: "Demo only", source_text: "Demo", dose_amount: null, dose_unit: null, administrations: [], days_of_week: null, duration_days: null, confidence: 95, field_reviews: [], ...values };
}

test("preserve unequal per-use doses; never guess exact clock or days", () => {
  const row = createReviewMedication(source({ administrations: [
    { label: "Sáng", dose_amount: 0.5, dose_unit: "viên", time_of_day: null },
    { label: "Tối", dose_amount: 2, dose_unit: "viên", time_of_day: "20:00" },
  ] }), "demo");
  assert.equal(row.administrations[0].doseAmount, "0.5");
  assert.equal(row.administrations[1].doseAmount, "2");
  assert.equal(row.administrations[0].time, "");
  assert.equal(row.days.length, 0);
  assert.match(validateReviewMedication(row, "2026-10-04"), /ngày dùng/);
  assert.notEqual(row.administrations[0].requestKey, row.administrations[1].requestKey);
});

test("duration derives inclusive end date and saved schedule retains every field", () => {
  assert.equal(endDateForDuration("2026-12-29", "7"), "2027-01-04");
  assert.equal(endDateForDuration("2026-10-04", "1"), "2026-10-04");
  assert.equal(endDateForDuration("2026-10-04", "0"), "");
  assert.equal(endDateForDuration("2026-10-04", "7.5"), "");
  assert.equal(endDateForDuration("2026-02-31", "7"), "");
  assert.equal(endDateForDuration("9999-12-31", "7"), "");
  const row = createReviewMedication(source({
    dose_amount: 1, dose_unit: "ml", duration_days: 7, days_of_week: [0, 2, 4],
    administrations: [{ label: "08:00", dose_amount: 1, dose_unit: "ml", time_of_day: "08:00" }],
  }), "demo");
  assert.equal(validateReviewMedication(row, "2026-10-04"), null);
  const schedule = scheduleFromReview(row, row.administrations[0], "2026-10-04", "helper");
  assert.equal(schedule.dose_amount, 1);
  assert.equal(schedule.time_of_day, "08:00:00");
  assert.equal(schedule.end_date, "2026-10-10");
  assert.equal(JSON.stringify(schedule.days_of_week), "[0,2,4]");
  assert.equal(schedule.assigned_caregiver_user_id, "helper");
  row.endDate = "2026-10-08";
  assert.equal(scheduleFromReview(row, row.administrations[0], "2026-10-04", "").end_date, "2026-10-08");
});

test("invalid amount, missing unit, ambiguous hours and repeated times block saving", () => {
  const row = createReviewMedication(source({ dose_amount: 1, dose_unit: "viên", days_of_week: [0] }), "demo");
  const slot = row.administrations[0];
  slot.time = "25:00";
  assert.match(validateReviewMedication(row, "2026-10-04"), /giờ cụ thể/);
  slot.time = "08:00";
  slot.doseAmount = "0.001";
  assert.match(validateReviewMedication(row, "2026-10-04"), /liều dùng/);
  slot.doseAmount = "0";
  assert.match(validateReviewMedication(row, "2026-10-04"), /liều dùng/);
  slot.doseAmount = "1";
  slot.doseUnit = "";
  assert.match(validateReviewMedication(row, "2026-10-04"), /đơn vị/);
  slot.doseUnit = "viên";
  row.administrations.push({ ...slot, id: "other" });
  assert.match(validateReviewMedication(row, "2026-10-04"), /trùng giờ/);
});

test("conditional/manual drafts do not silently become daily schedules", () => {
  const row = createReviewMedication(null, "manual");
  assert.equal(row.days.length, 0);
  assert.equal(row.administrations[0].doseAmount, "");
  assert.equal(row.administrations[0].doseUnit, "");
  assert.equal(row.administrations[0].time, "");
  assert.equal(row.durationDays, "");
  assert.equal(row.endDate, "");
});

test("deleting an edited slot cannot clear another slot's uncertainty", () => {
  const row = createReviewMedication(source({
    administrations: [
      { label: "Sáng", dose_amount: 1, dose_unit: "viên", time_of_day: "08:00" },
      { label: "Tối", dose_amount: 2, dose_unit: "viên", time_of_day: "20:00" },
    ],
    field_reviews: [{ field: "dose_amount", reason: "Kiểm tra từng liều" }],
  }), "demo");
  const firstKey = `slot-${row.administrations[0].id}:dose_amount`;
  row.editedFields.push(firstKey);
  assert.equal(reviewFieldHint(row, firstKey), undefined);
  row.administrations.shift();
  const remainingKey = `slot-${row.administrations[0].id}:dose_amount`;
  assert.equal(reviewFieldHint(row, remainingKey), "Kiểm tra từng liều");
  assert.ok(reviewFieldHint(row, firstKey, true), "an emptied edited field still needs a warning");
});

test("identical OCR rows cannot create duplicate reminders; different doses stay valid", () => {
  const data = source({ dose_amount: 1, dose_unit: "viên", days_of_week: [0, 1], administrations: [{ label: "Sáng", dose_amount: 1, dose_unit: "viên", time_of_day: "08:00" }] });
  const first = createReviewMedication(data, "first");
  const second = createReviewMedication(data, "second");
  assert.match(duplicateReviewSchedule([first, second], "2026-10-10", ""), /lặp lại/);
  second.included = false;
  assert.equal(duplicateReviewSchedule([first, second], "2026-10-10", ""), null);
  second.included = true;
  second.administrations[0].doseAmount = "2";
  assert.equal(duplicateReviewSchedule([first, second], "2026-10-10", ""), null);
});
