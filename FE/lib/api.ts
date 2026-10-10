import type {
  ApiErrorBody,
  CaregiverAssignment,
  CareGroupMember,
  CurrentUser,
  DoseOccurrence,
  Elder,
  Invitation,
  InvitationInspection,
  InvitationSummary,
  MedicationSchedule,
  PrescriptionOcrResult,
  TelegramLink,
} from "@/lib/types";

type AuthResponse = {
  user: {
    id: string;
    full_name: string;
    email: string;
    phone?: string | null;
    telegram_linked?: boolean;
  };
  groups: Array<{ id: string; name: string; role: "OWNER" | "CAREGIVER" }>;
  default_group_id: string | null;
};

export const SESSION_EXPIRED_EVENT = "healthguard:session-expired";
export const GROUP_ACCESS_CHANGED_EVENT = "healthguard:group-access-changed";
let authVersion = 0;
export function markAuthChanged(): void { authVersion += 1; }

export function storedGroupId(): string | null {
  try { return typeof window === "undefined" ? null : window.localStorage.getItem("healthguard_group_id"); }
  catch { return null; }
}

export function storeGroupId(value: string | null): void {
  try {
    if (typeof window === "undefined") return;
    if (value) window.localStorage.setItem("healthguard_group_id", value);
    else window.localStorage.removeItem("healthguard_group_id");
  } catch { /* Cookie authentication still works when browser storage is disabled. */ }
}

function normalizeAuth(payload: AuthResponse): CurrentUser {
  const groups = payload.groups.map((item) => ({
    care_group_id: item.id,
    care_group_name: item.name,
    role: item.role,
  }));
  const savedGroupId = storedGroupId();
  const group = groups.find((item) => item.care_group_id === savedGroupId)
    ?? groups.find((item) => item.care_group_id === payload.default_group_id)
    ?? groups[0]
    ?? null;
  return {
    ...payload.user,
    groups,
    current_group: group,
  };
}

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code = "REQUEST_FAILED",
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type RequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const requestAuthVersion = authVersion;
  const headers = new Headers(options.headers);
  const isFile = options.body instanceof Blob;
  if (options.body !== undefined) headers.set("Content-Type", isFile ? (options.body as Blob).type || "application/octet-stream" : "application/json");
  if (typeof window !== "undefined") {
    const groupId = storedGroupId();
    if (groupId) headers.set("X-Care-Group-ID", groupId);
  }

  let response: Response;
  const fetchOptions: RequestInit = {
    ...options, headers, credentials: "include", cache: "no-store",
    body: options.body === undefined ? undefined : isFile ? options.body as Blob : JSON.stringify(options.body),
  };
  try {
    response = await fetch(`${API_URL}${path}`, fetchOptions);
  } catch (caught) {
    const transientRead = (options.method ?? "GET").toUpperCase() === "GET"
      && !options.signal?.aborted && caught instanceof TypeError;
    if (transientRead) {
      await new Promise(resolve => setTimeout(resolve, 150));
      if (options.signal?.aborted) throw caught;
      try { response = await fetch(`${API_URL}${path}`, fetchOptions); }
      catch { throw new ApiError("Không thể kết nối máy chủ. Hãy kiểm tra backend đang chạy.", 0, "NETWORK_ERROR"); }
    } else throw new ApiError("Không thể kết nối máy chủ. Hãy kiểm tra backend đang chạy.", 0, "NETWORK_ERROR");
  }

  if (!response.ok) {
    let payload: ApiErrorBody | null = null;
    try {
      payload = (await response.json()) as ApiErrorBody;
    } catch {
      // Response may not contain JSON.
    }
    const detail = Array.isArray(payload?.detail)
      ? payload.detail.map((item) => item.msg).filter(Boolean).join(", ")
      : payload?.detail;
    if (typeof window !== "undefined" && requestAuthVersion === authVersion) {
      if (response.status === 401 && ["NOT_AUTHENTICATED", "INVALID_SESSION"].includes(payload?.error?.code ?? "")) {
        storeGroupId(null);
        window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
      } else if (response.status === 403 && payload?.error?.code === "GROUP_ACCESS_DENIED") {
        window.dispatchEvent(new Event(GROUP_ACCESS_CHANGED_EVENT));
      }
    }
    throw new ApiError(
      payload?.error?.message ?? detail ?? "Yêu cầu chưa thực hiện được.",
      response.status,
      payload?.error?.code,
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const authApi = {
  updateProfile: async (body: { full_name: string; phone: string | null }) =>
    normalizeAuth(await request<AuthResponse>("/auth/me", { method: "PATCH", body })),
  changePassword: (body: { current_password: string; new_password: string }) =>
    request<{ message: string }>("/auth/change-password", { method: "POST", body }),
  me: async () => normalizeAuth(await request<AuthResponse>("/auth/me")),
  login: async (body: { email: string; password: string }) =>
    normalizeAuth(await request<AuthResponse>("/auth/login", { method: "POST", body })),
  register: (body: {
    full_name: string;
    email: string;
    password: string;
  }) => request<AuthResponse>("/auth/register", { method: "POST", body }).then(normalizeAuth),
  registerCaregiver: (body: {
    full_name: string;
    email: string;
    password: string;
    invitation_token: string;
  }) => request<AuthResponse>("/auth/register-caregiver", { method: "POST", body }).then(normalizeAuth),
  inspectInvitation: (token: string) =>
    request<InvitationInspection>(`/invitations/${encodeURIComponent(token)}`),
  acceptInvitation: (token: string) =>
    request<{ message: string }>(`/invitations/${encodeURIComponent(token)}/accept`, {
      method: "POST",
    }),
  createTelegramLink: () =>
    request<TelegramLink>("/auth/telegram-link", { method: "POST" }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
};

export const eldersApi = {
  list: () => request<Elder[]>("/elders"),
  create: (body: Omit<Elder, "id" | "group_id" | "is_active" | "can_view_medications" | "can_confirm_doses" | "can_view_diagnoses">) =>
    request<Elder>("/elders", { method: "POST", body }),
  update: (id: string, body: Partial<Omit<Elder, "id" | "group_id" | "is_active" | "can_view_medications" | "can_confirm_doses" | "can_view_diagnoses">>) =>
    request<Elder>(`/elders/${id}`, { method: "PATCH", body }),
};

export const careApi = {
  createGroup: (name: string) =>
    request<{ id: string; name: string; role: "OWNER" }>("/care-groups", {
      method: "POST",
      body: { name },
    }),
  members: () => request<CareGroupMember[]>("/care-groups/current/members"),
  invitations: () =>
    request<InvitationSummary[]>("/care-groups/current/invitations"),
  invite: (email: string) =>
    request<Invitation>("/care-groups/current/invitations", {
      method: "POST",
      body: { email },
    }),
  revokeInvitation: (invitationId: string) =>
    request<void>(`/care-groups/current/invitations/${invitationId}`, { method: "DELETE" }),
  removeMember: (userId: string) =>
    request<void>(`/care-groups/current/members/${userId}`, { method: "DELETE" }),
  assign: (
    elderId: string,
    userId: string,
    permissions: {
      can_view_medications: boolean;
      can_confirm_doses: boolean;
      can_view_diagnoses: boolean;
    },
  ) =>
    request<CaregiverAssignment>(`/elders/${elderId}/caregivers/${userId}`, {
      method: "PUT",
      body: permissions,
    }),
  unassign: (elderId: string, userId: string) =>
    request<void>(`/elders/${elderId}/caregivers/${userId}`, { method: "DELETE" }),
  assignments: (elderId: string) =>
    request<CaregiverAssignment[]>(`/elders/${elderId}/caregivers`),
};

export const medicationApi = {
  readPrescription: (elderId: string, image: File) =>
    request<PrescriptionOcrResult>(`/elders/${elderId}/prescription-ocr?consent=true`, { method: "POST", body: image }),
  list: (elderId: string) =>
    request<MedicationSchedule[]>(`/elders/${elderId}/medication-schedules`),
  create: (elderId: string, body: Omit<MedicationSchedule, "id" | "elder_id" | "medication_id" | "is_active">, idempotencyKey?: string) =>
    request<MedicationSchedule>(`/elders/${elderId}/medication-schedules`, {
      method: "POST",
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
      body,
    }),
  update: (
    scheduleId: string,
    body: Pick<
      MedicationSchedule,
      | "medication_name"
      | "dose_amount"
      | "dose_unit"
      | "instructions"
      | "end_date"
      | "time_of_day"
      | "days_of_week"
      | "timezone"
      | "reminder_offsets_minutes"
      | "escalation_after_minutes"
      | "assigned_caregiver_user_id"
    >,
  ) => request<MedicationSchedule>(`/medication-schedules/${scheduleId}`, { method: "PATCH", body }),
  stop: (scheduleId: string) =>
    request<void>(`/medication-schedules/${scheduleId}`, { method: "DELETE" }),
};

export const dosesApi = {
  byDate: (date: string) =>
    request<DoseOccurrence[]>(`/dose-occurrences?date=${encodeURIComponent(date)}`),
  respond: (
    occurrenceId: string,
    body:
      | { status: "ADMINISTERED"; administered_at: string; note?: string | null }
      | { status: "CANNOT_ADMINISTER"; reason_code: string; note?: string | null },
  ) => request<DoseOccurrence>(`/dose-occurrences/${occurrenceId}/responses`, { method: "POST", body }),
};

export function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Đã có lỗi xảy ra.";
}
