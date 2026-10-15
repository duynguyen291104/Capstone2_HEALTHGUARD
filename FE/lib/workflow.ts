// Shared validation for the manual forms and asynchronous page refreshes.
export class RequestSequence {
  private version = 0;
  start() { return ++this.version; }
  invalidate() { this.version += 1; }
  isCurrent(version: number) { return version === this.version; }
}

export function createRequestKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  // getRandomValues is available on local-network HTTP as well as HTTPS.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map(value => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function responseActorName(
  response: { responded_by_user_id: string; responded_by_name?: string | null },
  currentUser?: { id: string; full_name: string } | null,
): string {
  if (response.responded_by_name?.trim()) return response.responded_by_name.trim();
  if (currentUser?.id === response.responded_by_user_id && currentUser.full_name.trim()) return currentUser.full_name.trim();
  return `Tài khoản ${response.responded_by_user_id.slice(0, 8)}`;
}

export function isCalendarDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function safeNextPath(value: string): string {
  if (!value.startsWith("/") || value.startsWith("//") || /[\\\u0000-\u001f\u007f]/.test(value)) return "";
  const base = "https://healthguard.invalid";
  try {
    const url = new URL(value, base);
    return url.origin === base ? `${url.pathname}${url.search}${url.hash}` : "";
  } catch { return ""; }
}

export function validatePasswordChange(current: string, next: string, confirmation: string): string | null {
  if (!current || current.length > 128) return "Hãy nhập mật khẩu hiện tại hợp lệ.";
  if (next.length < 10 || next.length > 128) return "Mật khẩu mới cần từ 10 đến 128 ký tự.";
  if (next !== confirmation) return "Mật khẩu nhập lại chưa khớp.";
  if (next === current) return "Mật khẩu mới phải khác mật khẩu hiện tại.";
  return null;
}

export type ScheduleValues = {
  name: string; amount: string; unit: string; time: string; days: number[];
  startDate: string; endDate: string; instructions: string;
};

export function validateScheduleValues(values: ScheduleValues): string | null {
  if (!values.name.trim() || values.name.trim().length > 200) return "Tên thuốc cần từ 1 đến 200 ký tự.";
  const amount = Number(values.amount);
  if (!values.amount.trim() || !Number.isFinite(amount) || amount < .01 || amount > 999999.99 || Math.abs(amount * 100 - Math.round(amount * 100)) > .000001) return "Liều dùng phải lớn hơn 0, tối đa 2 chữ số thập phân.";
  if (!values.unit.trim() || values.unit.trim().length > 50) return "Đơn vị cần từ 1 đến 50 ký tự.";
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(values.time)) return "Hãy chọn giờ dùng thuốc hợp lệ.";
  if (!values.days.length || values.days.some(day => !Number.isInteger(day) || day < 0 || day > 6)) return "Hãy chọn ít nhất một ngày dùng trong tuần.";
  if (!isCalendarDate(values.startDate)) return "Hãy chọn ngày bắt đầu hợp lệ.";
  if (values.endDate && (!isCalendarDate(values.endDate) || values.endDate < values.startDate)) return "Ngày kết thúc phải hợp lệ và không trước ngày bắt đầu.";
  if (values.instructions.length > 3000) return "Hướng dẫn tối đa 3.000 ký tự.";
  return null;
}
