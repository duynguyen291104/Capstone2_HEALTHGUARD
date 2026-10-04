import type { MedicationSchedule, PrescriptionDraft } from "@/lib/types";

export type ReviewAdministration = {
  id: string;
  requestKey: string;
  label: string;
  doseAmount: string;
  doseUnit: string;
  time: string;
};

export type ReviewMedication = {
  id: string;
  included: boolean;
  medicationName: string;
  instructions: string;
  administrations: ReviewAdministration[];
  days: number[];
  durationDays: string;
  endDate: string;
  source: PrescriptionDraft | null;
  editedFields: string[];
};

export const weekDays = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"];

export function reviewFieldHint(row: ReviewMedication, field: string, empty = false): string | undefined {
  const genericField = field.startsWith("slot-") ? field.split(":").at(-1) : field;
  const note = row.source?.field_reviews?.find(item => item.field === field || item.field === genericField);
  if (empty) return note?.reason ?? "Chưa có dữ liệu; cần bạn điền hoặc xác nhận theo đơn.";
  if (row.editedFields.includes(field)) return undefined;
  return note?.reason;
}

export function createReviewMedication(draft: PrescriptionDraft | null, id: string): ReviewMedication {
  const administrations = draft?.administrations?.length
    ? draft.administrations
    : [{ label: "Lần dùng 1", dose_amount: draft?.dose_amount ?? null, dose_unit: draft?.dose_unit ?? null, time_of_day: null }];
  return {
    id,
    included: true,
    medicationName: draft?.medication_name ?? "",
    instructions: draft?.instructions ?? "",
    administrations: administrations.map((item, index) => ({
      id: `${id}-${index}`,
      requestKey: crypto.randomUUID(),
      label: item.label,
      doseAmount: (item.dose_amount ?? draft?.dose_amount) == null ? "" : String(item.dose_amount ?? draft?.dose_amount),
      doseUnit: item.dose_unit ?? draft?.dose_unit ?? "",
      time: item.time_of_day?.slice(0, 5) ?? "",
    })),
    days: draft?.days_of_week ?? [],
    durationDays: draft?.duration_days ? String(draft.duration_days) : "",
    endDate: "",
    source: draft,
    editedFields: [],
  };
}

export function endDateForDuration(startDate: string, durationDays: string): string {
  const days = Number(durationDays);
  if (!startDate || !Number.isInteger(days) || days < 1 || days > 3650) return "";
  const value = new Date(`${startDate}T00:00:00Z`);
  if (!Number.isFinite(value.getTime())) return "";
  value.setUTCDate(value.getUTCDate() + days - 1);
  return value.toISOString().slice(0, 10);
}

export function validateReviewMedication(row: ReviewMedication, startDate: string): string | null {
  if (!row.medicationName.trim()) return "Điền tên thuốc.";
  if (row.medicationName.trim().length > 200) return "Tên thuốc tối đa 200 ký tự.";
  if (!startDate) return "Chọn ngày bắt đầu.";
  if (!row.days.length) return "Chọn ít nhất một ngày dùng trong tuần.";
  if (!row.administrations.length) return "Thêm ít nhất một lần dùng.";
  if (row.instructions.length > 3000) return "Hướng dẫn tối đa 3.000 ký tự.";
  if (row.durationDays && !endDateForDuration(startDate, row.durationDays)) return "Số ngày dùng phải là số nguyên từ 1 đến 3.650.";
  if (row.endDate && row.endDate < startDate) return "Ngày kết thúc không được trước ngày bắt đầu.";
  const times = new Set<string>();
  for (const slot of row.administrations) {
    const amount = Number(slot.doseAmount);
    if (!slot.doseAmount || !Number.isFinite(amount) || amount < 0.01 || amount > 999999.99 || Math.abs(amount * 100 - Math.round(amount * 100)) > 0.000001) return `${slot.label}: điền liều dùng hợp lệ, tối đa 2 chữ số thập phân.`;
    if (!slot.doseUnit.trim() || slot.doseUnit.trim().length > 50) return `${slot.label}: điền đơn vị (viên, ml, ống…).`;
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(slot.time)) return `${slot.label}: chọn giờ cụ thể.`;
    if (times.has(slot.time)) return "Hai lần dùng của cùng một thuốc đang trùng giờ; hãy kiểm tra lại.";
    times.add(slot.time);
  }
  return null;
}

export function scheduleFromReview(
  row: ReviewMedication,
  slot: ReviewAdministration,
  startDate: string,
  caregiverId: string,
): Omit<MedicationSchedule, "id" | "elder_id" | "medication_id" | "is_active"> {
  return {
    medication_name: row.medicationName.trim(),
    dose_amount: Number(slot.doseAmount),
    dose_unit: slot.doseUnit.trim(),
    instructions: row.instructions.trim() || null,
    time_of_day: `${slot.time}:00`,
    start_date: startDate,
    end_date: row.endDate || endDateForDuration(startDate, row.durationDays) || null,
    days_of_week: [...row.days],
    timezone: "Asia/Ho_Chi_Minh",
    reminder_offsets_minutes: [0, 15, 30, 45],
    escalation_after_minutes: 60,
    assigned_caregiver_user_id: caregiverId || null,
  };
}
