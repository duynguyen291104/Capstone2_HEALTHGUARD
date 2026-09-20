"use client";

import { AlarmClock, CalendarRange, CircleStop, Clock3, Pill, Plus, UserRound } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useAuth } from "@/components/auth-provider";
import { Button, EmptyState, ErrorNotice, Field, Modal, PageHeader, SelectField, SkeletonList, TextareaField } from "@/components/ui";
import { careApi, eldersApi, getErrorMessage, medicationApi } from "@/lib/api";
import type { CareGroupMember, Elder, MedicationSchedule } from "@/lib/types";

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

function ScheduleForm({
  elderId,
  caregivers,
  onSaved,
  onClose,
}: {
  elderId: string;
  caregivers: CareGroupMember[];
  onSaved: (schedule: MedicationSchedule) => void;
  onClose: () => void;
}) {
  const today = new Date().toISOString().slice(0, 10);
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [unit, setUnit] = useState("viên");
  const [time, setTime] = useState("08:00");
  const [days, setDays] = useState<number[]>(weekdayOptions.map((day) => day.value));
  const [startDate, setStartDate] = useState(today);
  const [endDate, setEndDate] = useState("");
  const [instructions, setInstructions] = useState("");
  const [caregiverId, setCaregiverId] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  function toggleDay(day: number) {
    setDays((current) => current.includes(day) ? current.filter((item) => item !== day) : [...current, day].sort());
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || !amount || !unit.trim()) return setError("Hãy nhập tên thuốc, liều lượng và đơn vị.");
    if (!days.length) return setError("Hãy chọn ít nhất một ngày uống trong tuần.");
    setSaving(true);
    setError("");
    try {
      const saved = await medicationApi.create(elderId, {
        medication_name: name.trim(),
        dose_amount: Number(amount),
        dose_unit: unit.trim(),
        instructions: instructions.trim() || null,
        time_of_day: `${time}:00`,
        days_of_week: days,
        start_date: startDate,
        end_date: endDate || null,
        timezone: "Asia/Bangkok",
        reminder_offsets_minutes: [0, 15, 30, 45],
        escalation_after_minutes: 60,
        assigned_caregiver_user_id: caregiverId || null,
      });
      onSaved(saved);
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="form-grid" onSubmit={submit}>
      {error ? <div className="form-grid__full"><ErrorNotice message={error} /></div> : null}
      <Field label="Tên thuốc *" value={name} onChange={(event) => setName(event.target.value)} placeholder="Ví dụ: Metformin" required />
      <div className="field-pair field-pair--inline">
        <Field label="Liều lượng *" type="number" min="0.01" step="0.01" value={amount} onChange={(event) => setAmount(event.target.value)} required />
        <Field label="Đơn vị *" value={unit} onChange={(event) => setUnit(event.target.value)} placeholder="viên, ml…" required />
      </div>
      <Field label="Giờ uống *" type="time" value={time} onChange={(event) => setTime(event.target.value)} required />
      <SelectField label="Người phụ trách" value={caregiverId} onChange={(event) => setCaregiverId(event.target.value)}>
        <option value="">Chưa chỉ định</option>
        {caregivers.map((caregiver) => <option key={caregiver.user_id} value={caregiver.user_id}>{caregiver.full_name}</option>)}
      </SelectField>
      <fieldset className="weekday-fieldset form-grid__full">
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
      <Field label="Ngày bắt đầu *" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} required />
      <Field label="Ngày kết thúc" type="date" min={startDate} value={endDate} onChange={(event) => setEndDate(event.target.value)} hint="Để trống nếu dùng đến khi có chỉ định mới." />
      <div className="form-grid__full"><TextareaField label="Hướng dẫn theo đơn" value={instructions} onChange={(event) => setInstructions(event.target.value)} placeholder="Ví dụ: Uống sau bữa sáng" /></div>
      <div className="reminder-summary form-grid__full"><AlarmClock size={19} /><p><strong>Quy tắc nhắc mặc định:</strong> đúng giờ, sau 15, 30 và 45 phút. Sau 60 phút chưa có phản hồi, hệ thống chuyển thành “Chưa xác nhận”.</p></div>
      <div className="form-actions form-grid__full"><Button type="button" variant="secondary" onClick={onClose}>Hủy</Button><Button type="submit" loading={saving}>Tạo lịch thuốc</Button></div>
    </form>
  );
}

export function MedicationsPage() {
  const { user } = useAuth();
  const [elders, setElders] = useState<Elder[]>([]);
  const [caregivers, setCaregivers] = useState<CareGroupMember[]>([]);
  const [elderId, setElderId] = useState("");
  const [schedules, setSchedules] = useState<MedicationSchedule[]>([]);
  const [initialLoading, setInitialLoading] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [stopping, setStopping] = useState<string | null>(null);
  const isOwner = user?.current_group.role === "OWNER";

  useEffect(() => {
    async function initialize() {
      setInitialLoading(true);
      setError("");
      try {
        const elderList = await eldersApi.list();
        setElders(elderList);
        setElderId(elderList[0]?.id ?? "");
        if (isOwner) {
          const members = await careApi.members();
          setCaregivers(members.filter((member) => member.role === "CAREGIVER"));
        }
      } catch (caught) {
        setError(getErrorMessage(caught));
      } finally {
        setInitialLoading(false);
      }
    }
    void initialize();
  }, [isOwner]);

  useEffect(() => {
    if (!elderId) return;
    let active = true;
    medicationApi.list(elderId)
      .then((items) => { if (active) setSchedules(items); })
      .catch((caught) => { if (active) setError(getErrorMessage(caught)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [elderId]);

  const selectedElder = useMemo(() => elders.find((elder) => elder.id === elderId), [elderId, elders]);

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

  if (initialLoading) return <><PageHeader title="Thuốc & lịch uống" description="Đang tải hồ sơ và lịch thuốc…" /><SkeletonList /></>;

  return (
    <>
      <PageHeader
        eyebrow="Thiết lập theo đơn"
        title="Thuốc & lịch uống"
        description="Lưu lịch một lần; chỉ chỉnh khi đơn thuốc hoặc người phụ trách thay đổi."
        action={isOwner && elderId ? <Button onClick={() => setCreating(true)}><Plus size={18} /> Tạo lịch thuốc</Button> : undefined}
      />
      {elders.length ? (
        <div className="toolbar-card">
          <SelectField label="Đang xem lịch của" value={elderId} onChange={(event) => { setLoading(true); setSchedules([]); setElderId(event.target.value); }}>
            {elders.map((elder) => <option key={elder.id} value={elder.id}>{elder.full_name}</option>)}
          </SelectField>
          <p><Pill size={17} /> Chỉ nhập thuốc và liều lượng đúng theo đơn hoặc hướng dẫn của nhân viên y tế.</p>
        </div>
      ) : null}
      {error ? <ErrorNotice message={error} /> : null}
      {!elders.length ? (
        <EmptyState title="Chưa có người được chăm sóc" description="Hãy tạo hồ sơ người cao tuổi trước khi thiết lập lịch thuốc." />
      ) : loading ? <SkeletonList /> : schedules.length === 0 ? (
        <EmptyState title={`Chưa có lịch thuốc cho ${selectedElder?.full_name ?? "hồ sơ này"}`} description={isOwner ? "Tạo lịch đầu tiên từ thông tin trên đơn thuốc." : "Chủ gia đình chưa tạo lịch thuốc cho hồ sơ này."} action={isOwner ? <Button onClick={() => setCreating(true)}><Plus size={18} /> Tạo lịch đầu tiên</Button> : undefined} />
      ) : (
        <div className="schedule-list">
          {schedules.map((schedule) => (
            <article className={`schedule-card ${!schedule.is_active ? "schedule-card--inactive" : ""}`} key={schedule.id}>
              <div className="schedule-card__time"><Clock3 size={19} /><strong>{schedule.time_of_day.slice(0, 5)}</strong><span>{formatDays(schedule.days_of_week)}</span></div>
              <div className="schedule-card__main">
                <div><span className="pill-icon"><Pill size={20} /></span><div><h2>{schedule.medication_name}</h2><p>{schedule.dose_amount} {schedule.dose_unit}{schedule.instructions ? ` · ${schedule.instructions}` : ""}</p></div></div>
                <div className="schedule-card__meta"><span><CalendarRange size={16} /> {formatDate(schedule.start_date)} → {formatDate(schedule.end_date)}</span><span><UserRound size={16} /> {caregivers.find((item) => item.user_id === schedule.assigned_caregiver_user_id)?.full_name ?? "Chưa chỉ định"}</span></div>
              </div>
              <div className="schedule-card__actions">
                <span className={schedule.is_active ? "status-dot status-dot--active" : "status-dot"}>{schedule.is_active ? "Đang hoạt động" : "Đã ngừng"}</span>
                {isOwner && schedule.is_active ? <Button type="button" variant="ghost" loading={stopping === schedule.id} onClick={() => stop(schedule)}><CircleStop size={17} /> Ngừng lịch</Button> : null}
              </div>
            </article>
          ))}
        </div>
      )}
      {creating ? (
        <Modal title={`Tạo lịch thuốc${selectedElder ? ` cho ${selectedElder.full_name}` : ""}`} description="Hệ thống sẽ tạo từng lần cần uống dựa trên lịch này." onClose={() => setCreating(false)}>
          <ScheduleForm elderId={elderId} caregivers={caregivers} onSaved={(schedule) => { setSchedules((current) => [schedule, ...current]); setCreating(false); }} onClose={() => setCreating(false)} />
        </Modal>
      ) : null}
    </>
  );
}
