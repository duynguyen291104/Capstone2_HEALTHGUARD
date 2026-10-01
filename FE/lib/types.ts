export type AppRole = "OWNER" | "CAREGIVER";

export type Membership = {
  care_group_id: string;
  care_group_name: string;
  role: AppRole;
};

export type CurrentUser = {
  id: string;
  full_name: string;
  email: string;
  phone?: string | null;
  telegram_linked?: boolean;
  groups: Membership[];
  current_group: Membership | null;
};

export type Elder = {
  id: string;
  group_id: string;
  full_name: string;
  date_of_birth: string | null;
  sex: string | null;
  height_cm: number | null;
  weight_kg: number | null;
  diagnosed_conditions: string[] | null;
  current_medications_note: string | null;
  mobility_level: string | null;
  sleep_habits: string | null;
  emergency_contact_name: string | null;
  emergency_contact_phone: string | null;
  is_active: boolean;
  can_view_medications: boolean;
  can_confirm_doses: boolean;
  can_view_diagnoses: boolean;
};

export type CareGroupMember = {
  user_id: string;
  full_name: string;
  email: string;
  role: AppRole;
  joined_at?: string;
  telegram_linked: boolean;
};

export type Invitation = {
  id: string;
  email: string;
  expires_at: string;
  invitation_url?: string;
  invitation_token: string;
};

export type InvitationInspection = {
  email: string;
  care_group_name: string;
  expires_at: string;
  valid: boolean;
};

export type InvitationSummary = {
  id: string;
  email: string;
  role: "CAREGIVER";
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
  created_at: string;
};

export type TelegramLink = {
  deep_link: string;
  expires_at: string;
};

export type CaregiverAssignment = {
  elder_id: string;
  caregiver_user_id: string;
  can_view_medications: boolean;
  can_confirm_doses: boolean;
  can_view_diagnoses: boolean;
  created_at: string;
};

export type MedicationSchedule = {
  id: string;
  elder_id: string;
  medication_id: string;
  medication_name: string;
  dose_amount: number;
  dose_unit: string;
  instructions: string | null;
  time_of_day: string;
  days_of_week: number[];
  start_date: string;
  end_date: string | null;
  timezone: string;
  reminder_offsets_minutes: number[];
  escalation_after_minutes: number;
  assigned_caregiver_user_id: string | null;
  is_active: boolean;
};

export type DoseStatus =
  | "SCHEDULED"
  | "DUE"
  | "ADMINISTERED"
  | "CANNOT_ADMINISTER"
  | "UNCONFIRMED"
  | "CANCELLED";

export type DoseOccurrence = {
  id: string;
  elder_id: string;
  elder_name: string;
  schedule_id: string;
  medication_name: string;
  dose_amount: number;
  dose_unit: string;
  instructions: string | null;
  scheduled_for: string;
  status: DoseStatus;
  reminder_count: number;
  can_respond: boolean;
  response?: {
    response_type: "ADMINISTERED" | "CANNOT_ADMINISTER";
    responded_by_user_id: string;
    administered_at: string | null;
    responded_at: string;
    reason: string | null;
    notes: string | null;
  } | null;
};

export type ApiErrorBody = {
  error?: {
    code?: string;
    message?: string;
    fields?: Array<{ field: string; message: string }> | Record<string, string[]> | null;
  };
  detail?: string | Array<{ msg?: string }>;
};
