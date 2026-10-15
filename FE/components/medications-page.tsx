"use client";

import { AlarmClock, CalendarRange, CircleStop, Clock3, Pencil, Pill, Plus, UserRound } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@/components/auth-provider";
import { Button, EmptyState, ErrorNotice, Field, Modal, PageHeader, SelectField, SkeletonList, TextareaField } from "@/components/ui";
import { careApi, eldersApi, getErrorMessage, medicationApi } from "@/lib/api";
import type { CaregiverAssignment, CareGroupMember, Elder, MedicationSchedule } from "@/lib/types";
import { PrescriptionImport } from "@/components/prescription-import";
import { createRequestKey, validateScheduleValues } from "@/lib/workflow";

const weekdayOptions = [
  { value: 0, short: "T2", label: "Thứ Hai" },
  { value: 1, short: "T3", label: "Thứ Ba" },
  { value: 2, short: "T4", label: "Thứ Tư" },
  { value: 3, short: "T5", label: "Thứ Năm" },
  { value: 4, short: "T6", label: "Thứ Sáu" },
  { value: 5, short: "T7", label: "Thứ Bảy" },
  { value: 6, short: "CN", label: "Chủ Nhật" },
];

function formatDays(days: number[]) {
  if (days.length === 7) return "Hằng ngày";
  return weekdayOptions.filter((day) => days.includes(day.value)).map((day) => day.short).join(", ");
}

function formatDate(date: string | null) {
  if (!date) return "Không giới hạn";
  return new Intl.DateTimeFormat("vi-VN").format(new Date(`${date}T00:00:00`));
}

function vietnamToday() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Ho_Chi_Minh" }).format(new Date());
}

export function ScheduleForm({
  elderId,
  caregivers,
  schedule,
  draft,
  onSaved,
  onClose,
  onBusyChange,
}: {
  elderId: string;
  caregivers: CareGroupMember[];
  schedule?: MedicationSchedule;
  draft?: { medication_name: string; instructions: string };
  onSaved: (schedule: MedicationSchedule) => void;
  onClose: () => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const today = vietnamToday();
  const [name, setName] = useState(schedule?.medication_name ?? draft?.medication_name ?? "");
  const [amount, setAmount] = useState(schedule ? String(schedule.dose_amount) : "");
  const [unit, setUnit] = useState(schedule?.dose_unit ?? (draft ? "" : "viên"));
  const [time, setTime] = useState(schedule?.time_of_day.slice(0, 5) ?? (draft ? "" : "08:00"));
  const [days, setDays] = useState<number[]>(schedule ? [...schedule.days_of_week] : draft ? [] : weekdayOptions.map((day) => day.value));
  const [startDate, setStartDate] = useState(schedule?.start_date ?? (draft ? "" : today));
  const [endDate, setEndDate] = useState(schedule?.end_date ?? "");
  const [instructions, setInstructions] = useState(schedule?.instructions ?? draft?.instructions ?? "");
  const [reviewed, setReviewed] = useState(false);
  const [caregiverId, setCaregiverId] = useState(schedule?.assigned_caregiver_user_id ?? "");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const requestKey = useRef<string | null>(null);

  function toggleDay(day: number) {
    setDays((current) => current.includes(day) ? current.filter((item) => item !== day) : [...current, day].sort());
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (saving) return;
    if (draft && !reviewed) return setError("Hãy đối chiếu với đơn gốc và xác nhận trước khi tạo lịch.");
    const validation = validateScheduleValues({ name, amount, unit, time, days, startDate, endDate, instructions });
    if (validation) return setError(validation);
    if (caregiverId && !caregivers.some(item => item.user_id === caregiverId)) return setError("Người phụ trách không còn quyền xác nhận; hãy chọn lại hoặc để chưa chỉ định.");
    setSaving(true);
    onBusyChange?.(true);
    setError("");
    try {
      const editableValues = {
        medication_name: name.trim(),
        dose_amount: Number(amount),
        dose_unit: unit.trim(),
        instructions: instructions.trim() || null,
        time_of_day: `${time}:00`,
        days_of_week: days,
        start_date: startDate,
        end_date: endDate || null,
        timezone: schedule?.timezone ?? "Asia/Ho_Chi_Minh",
        reminder_offsets_minutes: schedule?.reminder_offsets_minutes ?? [0, 15, 30, 45],
        escalation_after_minutes: schedule?.escalation_after_minutes ?? 60,
        assigned_caregiver_user_id: caregiverId || null,
      };
      const saved = schedule
        ? await medicationApi.update(schedule.id, {
            medication_name: editableValues.medication_name,
            dose_amount: editableValues.dose_amount,
            dose_unit: editableValues.dose_unit,
            instructions: editableValues.instructions,
            time_of_day: editableValues.time_of_day,
            days_of_week: editableValues.days_of_week,
            end_date: editableValues.end_date,
            timezone: editableValues.timezone,
            reminder_offsets_minutes: editableValues.reminder_offsets_minutes,
            escalation_after_minutes: editableValues.escalation_after_minutes,
            assigned_caregiver_user_id: editableValues.assigned_caregiver_user_id,
          })
        : await medicationApi.create(elderId, { ...editableValues, start_date: startDate }, requestKey.current ?? (requestKey.current = createRequestKey()));
      onSaved(saved);
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setSaving(false);
      onBusyChange?.(false);
    }
  }

  return (
    <form className="form-grid" onSubmit={submit}>
      {error ? <div className="form-grid__full"><ErrorNotice message={error} /></div> : null}
      <Field label="Tên thuốc *" maxLength={200} disabled={saving} value={name} onChange={(event) => setName(event.target.value)} placeholder="Ví dụ: Metformin" required />
      <div className="field-pair field-pair--inline">
        <Field label="Liều lượng *" type="number" min="0.01" max="999999.99" step="0.01" disabled={saving} value={amount} onChange={(event) => setAmount(event.target.value)} required />
        <Field label="Đơn vị *" maxLength={50} disabled={saving} value={unit} onChange={(event) => setUnit(event.target.value)} placeholder="viên, ml…" required />
      </div>
      <Field label="Giờ uống *" type="time" disabled={saving} value={time} onChange={(event) => setTime(event.target.value)} required />
      <SelectField label="Người phụ trách" disabled={saving} value={caregiverId} onChange={(event) => setCaregiverId(event.target.value)}>
        <option value="">Chưa chỉ định</option>
        {caregiverId && !caregivers.some(item => item.user_id === caregiverId) ? <option value={caregiverId} disabled>Người phụ trách không còn quyền — hãy chọn lại</option> : null}
        {caregivers.map((caregiver) => <option key={caregiver.user_id} value={caregiver.user_id}>{caregiver.full_name}</option>)}
      </SelectField>
      <fieldset className="weekday-fieldset form-grid__full" disabled={saving}>
        <legend>Ngày uống trong tuần *</legend>
        <div className="weekday-picker">
          {weekdayOptions.map((day) => (
            <label key={day.value} title={day.label}>
              <input type="checkbox" checked={days.includes(day.value)} onChange={() => toggleDay(day.value)} />
              <span>{day.short}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <Field
        label="Ngày bắt đầu *"
        type="date"
        value={startDate}
        onChange={(event) => setStartDate(event.target.value)}
        disabled={Boolean(schedule) || saving}
        hint={schedule ? "Ngày bắt đầu được giữ nguyên để bảo toàn lịch sử." : undefined}
        required
      />
      <Field label="Ngày kết thúc" type="date" disabled={saving} min={startDate} value={endDate} onChange={(event) => setEndDate(event.target.value)} hint="Để trống nếu dùng đến khi có chỉ định mới." />
      <div className="form-grid__full"><TextareaField label="Hướng dẫn theo đơn" maxLength={3000} disabled={saving} value={instructions} onChange={(event) => setInstructions(event.target.value)} placeholder="Ví dụ: Uống sau bữa sáng" /></div>
      <div className="reminder-summary form-grid__full"><AlarmClock size={19} /><p><strong>Quy tắc nhắc mặc định:</strong> đúng giờ, sau 15, 30 và 45 phút. Sau 60 phút chưa có phản hồi, hệ thống chuyển thành “Chưa xác nhận”.</p></div>
      {draft ? <label className="settings-checkbox form-grid__full"><input type="checkbox" required checked={reviewed} onChange={event => setReviewed(event.target.checked)} /> Tôi đã đối chiếu đúng người, tên thuốc/hàm lượng, liều, ngày và giờ uống với đơn gốc.</label> : null}
      <div className="form-actions form-grid__full"><Button type="button" variant="secondary" disabled={saving} onClick={onClose}>Hủy</Button><Button type="submit" loading={saving}>{schedule ? "Lưu thay đổi" : "Tạo lịch thuốc"}</Button></div>
    </form>
  );
}

export function MedicationsPage() {
  const { user } = useAuth();
  const [elders, setElders] = useState<Elder[]>([]);
  const [caregivers, setCaregivers] = useState<CareGroupMember[]>([]);
  const [assignments, setAssignments] = useState<CaregiverAssignment[]>([]);
  const [elderId, setElderId] = useState("");
  const [schedules, setSchedules] = useState<MedicationSchedule[]>([]);
  const [initialLoading, setInitialLoading] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<MedicationSchedule | null>(null);
  const [stopping, setStopping] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [loadedElder, setLoadedElder] = useState("");
  const [formBusy, setFormBusy] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const selectedElderId = useRef("");
  const isOwner = user?.current_group?.role === "OWNER";

  useEffect(() => {
    let active = true;
    async function initialize() {
      setInitialLoading(true);
      setError("");
      try {
        const [elderList, members] = await Promise.all([eldersApi.list(), isOwner ? careApi.members() : Promise.resolve([])]);
        if (!active) return;
        const medicationElders = isOwner
          ? elderList
          : elderList.filter((elder) => elder.can_view_medications);
        setElders(medicationElders);
        setElderId(medicationElders[0]?.id ?? "");
        selectedElderId.current = medicationElders[0]?.id ?? "";
        setCaregivers(members.filter((member) => member.role === "CAREGIVER"));
      } catch (caught) {
        if (active) setError(getErrorMessage(caught));
      } finally {
        if (active) setInitialLoading(false);
      }
    }
    void initialize();
    return () => { active = false; };
  }, [isOwner, reload]);

  useEffect(() => {
    if (!elderId) return;
    let active = true;
    Promise.all([
      medicationApi.list(elderId),
      isOwner ? careApi.assignments(elderId) : Promise.resolve([]),
    ])
      .then(([items, assignmentList]) => {
        if (!active) return;
        setSchedules(items);
        setAssignments(assignmentList);
        setLoadedElder(elderId);
      })
      .catch((caught) => { if (active) { setSchedules([]); setAssignments([]); setLoadedElder(""); setError(getErrorMessage(caught)); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [elderId, isOwner, reload]);

  const selectedElder = useMemo(() => elders.find((elder) => elder.id === elderId), [elderId, elders]);
  const eligibleCaregivers = useMemo(
    () => caregivers.filter((caregiver) => assignments.some(
      (assignment) => assignment.caregiver_user_id === caregiver.user_id && assignment.can_view_medications && assignment.can_confirm_doses,
    )),
    [assignments, caregivers],
  );

  async function stop(schedule: MedicationSchedule) {
    if (!window.confirm(`Ngừng lịch ${schedule.medication_name}? Lịch sử cũ vẫn được giữ lại.`)) return;
    setStopping(schedule.id);
    setError("");
    try {
      await medicationApi.stop(schedule.id);
      setSchedules((current) => current.map((item) => item.id === schedule.id ? { ...item, is_active: false } : item));
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setStopping(null);
    }
  }

  function addSavedSchedule(saved: MedicationSchedule) {
    if (saved.elder_id !== selectedElderId.current) return;
    setSchedules(current => [saved, ...current.filter(item => item.id !== saved.id)]);
  }

  if (initialLoading) return <><PageHeader title="Thuốc & lịch uống" description="Đang tải hồ sơ và lịch thuốc…" /><SkeletonList /></>;
  if (error && !elders.length) return <><PageHeader title="Thuốc & lịch uống" description="Thiết lập thuốc và giờ dùng theo đơn." /><ErrorNotice message={error} /><Button type="button" onClick={() => setReload(value => value + 1)}>Tải lại</Button></>;

  return (
    <>
      <PageHeader
        eyebrow="Thiết lập theo đơn"
        title="Thuốc & lịch uống"
        description="Lưu lịch một lần; chỉ chỉnh khi đơn thuốc hoặc người phụ trách thay đổi."
        action={isOwner && elderId && loadedElder === elderId && !loading ? <Button onClick={() => setCreating(true)}><Plus size={18} /> Tạo lịch thuốc</Button> : undefined}
      />
      {elders.length ? (
        <div className="toolbar-card">
          <SelectField label="Đang xem lịch của" disabled={formBusy || importBusy} value={elderId} onChange={(event) => { selectedElderId.current = event.target.value; setError(""); setLoading(true); setSchedules([]); setElderId(event.target.value); }}>
            {elders.map((elder) => <option key={elder.id} value={elder.id}>{elder.full_name}</option>)}
          </SelectField>
          <p><Pill size={17} /> Chỉ nhập thuốc và liều lượng đúng theo đơn hoặc hướng dẫn của nhân viên y tế.</p>
        </div>
      ) : null}
      {error ? <ErrorNotice message={error} /> : null}
      {isOwner && elderId && loadedElder === elderId && !loading ? <PrescriptionImport key={elderId} elderId={elderId} elderName={selectedElder?.full_name ?? ""} caregivers={eligibleCaregivers} onBusyChange={setImportBusy} onSaved={addSavedSchedule} /> : null}
      {!elders.length ? (
        <EmptyState title={isOwner ? "Chưa có người được chăm sóc" : "Bạn chưa có quyền xem lịch thuốc"} description={isOwner ? "Hãy tạo hồ sơ người cao tuổi trước khi thiết lập lịch thuốc." : "Nhờ chủ gia đình phân công hồ sơ và cấp quyền xem lịch thuốc cho bạn."} />
      ) : loading ? <SkeletonList /> : loadedElder !== elderId ? error ? <Button type="button" onClick={() => setReload(value => value + 1)}>Tải lại lịch thuốc</Button> : <SkeletonList /> : schedules.length === 0 ? (
        <EmptyState title={`Chưa có lịch thuốc cho ${selectedElder?.full_name ?? "hồ sơ này"}`} description={isOwner ? "Tạo lịch đầu tiên từ thông tin trên đơn thuốc." : "Chủ gia đình chưa tạo lịch thuốc cho hồ sơ này."} action={isOwner ? <Button onClick={() => setCreating(true)}><Plus size={18} /> Tạo lịch đầu tiên</Button> : undefined} />
      ) : (
        <div className="schedule-list">
          {schedules.map((schedule) => (
            <article className={`schedule-card ${!schedule.is_active ? "schedule-card--inactive" : ""}`} key={schedule.id}>
              <div className="schedule-card__time"><Clock3 size={19} /><strong>{schedule.time_of_day.slice(0, 5)}</strong><span>{formatDays(schedule.days_of_week)}</span></div>
              <div className="schedule-card__main">
                <div><span className="pill-icon"><Pill size={20} /></span><div><h2>{schedule.medication_name}</h2><p>{schedule.dose_amount} {schedule.dose_unit}{schedule.instructions ? ` · ${schedule.instructions}` : ""}</p></div></div>
                <div className="schedule-card__meta"><span><CalendarRange size={16} /> {formatDate(schedule.start_date)} → {formatDate(schedule.end_date)}</span><span><UserRound size={16} /> {caregivers.find((item) => item.user_id === schedule.assigned_caregiver_user_id)?.full_name ?? (schedule.assigned_caregiver_user_id ? "Đã chỉ định người phụ trách" : "Chưa chỉ định")}</span></div>
              </div>
              <div className="schedule-card__actions">
                <span className={schedule.is_active ? "status-dot status-dot--active" : "status-dot"}>{schedule.is_active ? "Đang hoạt động" : "Đã ngừng"}</span>
                {isOwner && schedule.is_active ? <Button type="button" variant="ghost" onClick={() => setEditing(schedule)}><Pencil size={17} /> Sửa lịch</Button> : null}
                {isOwner && schedule.is_active ? <Button type="button" variant="ghost" loading={stopping === schedule.id} onClick={() => stop(schedule)}><CircleStop size={17} /> Ngừng lịch</Button> : null}
              </div>
            </article>
          ))}
        </div>
      )}
      {creating ? (
        <Modal dismissible={!formBusy} title={`Tạo lịch thuốc${selectedElder ? ` cho ${selectedElder.full_name}` : ""}`} description="Hệ thống sẽ tạo từng lần cần uống dựa trên lịch này." onClose={() => { if (!formBusy) setCreating(false); }}>
          <ScheduleForm elderId={elderId} caregivers={eligibleCaregivers} onBusyChange={setFormBusy} onSaved={(schedule) => { addSavedSchedule(schedule); setCreating(false); }} onClose={() => { if (!formBusy) setCreating(false); }} />
        </Modal>
      ) : null}
      {editing ? (
        <Modal dismissible={!formBusy} title={`Sửa lịch ${editing.medication_name}`} description="Nếu lần dùng hôm nay đã đến giờ, thay đổi áp dụng từ ngày mai. Các lần chưa đến giờ được cập nhật theo lịch mới; lịch sử cũ được giữ lại." onClose={() => { if (!formBusy) setEditing(null); }}>
          <ScheduleForm
            key={editing.id}
            elderId={elderId}
            caregivers={eligibleCaregivers}
            schedule={editing}
            onBusyChange={setFormBusy}
            onSaved={(saved) => {
              setSchedules((current) => current.map((item) => item.id === editing.id ? saved : item));
              setEditing(null);
            }}
            onClose={() => { if (!formBusy) setEditing(null); }}
          />
        </Modal>
      ) : null}
    </>
  );
}
