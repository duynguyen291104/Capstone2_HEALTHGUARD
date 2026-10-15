"use client";

import { AlertTriangle, CalendarDays, Check, CheckCircle2, Clock3, Pill, RefreshCw, XCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, EmptyState, ErrorNotice, Modal, PageHeader, SelectField, SkeletonList, TextareaField } from "@/components/ui";
import { dosesApi, getErrorMessage } from "@/lib/api";
import type { DoseOccurrence, DoseStatus } from "@/lib/types";
import { isCalendarDate, RequestSequence, responseActorName } from "@/lib/workflow";
import { useAuth } from "@/components/auth-provider";

const statusLabels: Record<DoseStatus, string> = {
  SCHEDULED: "Sắp tới",
  DUE: "Đến giờ",
  ADMINISTERED: "Đã xác nhận",
  CANNOT_ADMINISTER: "Chưa thể cho uống",
  UNCONFIRMED: "Chưa xác nhận",
  CANCELLED: "Đã hủy",
};

const reasonOptions = [
  { value: "ELDER_REFUSED", label: "Người được chăm sóc từ chối" },
  { value: "ELDER_ASLEEP", label: "Người được chăm sóc đang ngủ" },
  { value: "MEDICATION_UNAVAILABLE", label: "Không có sẵn thuốc" },
  { value: "AWAY_FROM_HOME", label: "Không có mặt tại nhà" },
  { value: "OTHER", label: "Lý do khác" },
];

function localDate() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Ho_Chi_Minh" }).format(new Date());
}

function longDate(date: string) {
  return new Intl.DateTimeFormat("vi-VN", { dateStyle: "full" }).format(new Date(`${date}T12:00:00`));
}

function timeOf(iso: string) {
  return new Intl.DateTimeFormat("vi-VN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Ho_Chi_Minh" }).format(new Date(iso));
}

function reasonLabel(reason: string) {
  return reasonOptions.find((option) => option.value === reason)?.label ?? reason;
}

function CannotAdministerForm({ dose, onSaved, onClose, onBusyChange }: { dose: DoseOccurrence; onSaved: (dose: DoseOccurrence) => void; onClose: () => void; onBusyChange: (busy: boolean) => void }) {
  const [reason, setReason] = useState(reasonOptions[0].value);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (saving) return;
    if (note.length > 2000) return setError("Ghi chú tối đa 2.000 ký tự.");
    if (reason === "OTHER" && !note.trim()) return setError("Hãy ghi rõ lý do khác để chủ gia đình kiểm tra.");
    setSaving(true);
    onBusyChange(true);
    setError("");
    try {
      onSaved(await dosesApi.respond(dose.id, { status: "CANNOT_ADMINISTER", reason_code: reason, note: note.trim() || null }));
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setSaving(false);
      onBusyChange(false);
    }
  }

  return (
    <form className="form-stack" onSubmit={submit}>
      {error ? <ErrorNotice message={error} /> : null}
      <SelectField label="Lý do" disabled={saving} value={reason} onChange={(event) => setReason(event.target.value)}>
        {reasonOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </SelectField>
      <TextareaField label="Ghi chú thêm" maxLength={2000} required={reason === "OTHER"} disabled={saving} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Thông tin giúp chủ gia đình kiểm tra lại…" />
      <div className="form-actions"><Button type="button" variant="secondary" disabled={saving} onClick={onClose}>Hủy</Button><Button type="submit" loading={saving}>Gửi phản hồi</Button></div>
    </form>
  );
}

function DoseCard({ dose, now, onUpdated, onCannot, highlighted }: { dose: DoseOccurrence; now: number | null; onUpdated: (dose: DoseOccurrence) => void; onCannot: () => void; highlighted?: boolean }) {
  const { user } = useAuth();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const scheduledFor = new Date(dose.scheduled_for).getTime();
  const actionable = dose.can_respond && (
    dose.status === "DUE"
    || dose.status === "UNCONFIRMED"
    || (dose.status === "SCHEDULED" && now !== null && Number.isFinite(scheduledFor) && scheduledFor <= now)
  );

  async function confirm() {
    if (confirming || !actionable) return;
    setConfirming(true);
    setError("");
    try {
      onUpdated(await dosesApi.respond(dose.id, { status: "ADMINISTERED", administered_at: new Date().toISOString(), note: null }));
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setConfirming(false);
    }
  }

  return (
    <article id={`dose-${dose.id}`} className={`dose-card dose-card--${dose.status.toLowerCase()} ${highlighted ? "dose-card--highlighted" : ""}`}>
      <div className="dose-card__time"><strong>{timeOf(dose.scheduled_for)}</strong><span>{statusLabels[dose.status]}</span></div>
      <div className="dose-card__medicine">
        <span className="pill-icon"><Pill size={21} /></span>
        <div>
          <h2>{dose.medication_name}</h2>
          <p>{dose.dose_amount} {dose.dose_unit} · {dose.elder_name}</p>
          {dose.instructions ? <p>Hướng dẫn: {dose.instructions}</p> : null}
        </div>
      </div>
      <div className="dose-card__status">
        {dose.status === "ADMINISTERED" ? <CheckCircle2 size={19} /> : dose.status === "CANNOT_ADMINISTER" || dose.status === "UNCONFIRMED" ? <AlertTriangle size={19} /> : <Clock3 size={19} />}
        <span>
          <strong>{statusLabels[dose.status]}</strong>
          {dose.response ? <small style={{ overflowWrap: "anywhere" }}>Xác nhận bởi {responseActorName(dose.response, user)}</small> : null}
          {dose.response?.responded_at ? <small>Lúc {timeOf(dose.response.responded_at)}</small> : dose.status === "UNCONFIRMED" ? <small>Cần liên hệ để kiểm tra</small> : null}
          {dose.response?.reason ? <small>Lý do: {reasonLabel(dose.response.reason)}</small> : null}
          {dose.response?.notes ? <small>Ghi chú: {dose.response.notes}</small> : null}
        </span>
      </div>
      {actionable ? (
        <div className="dose-card__actions">
          <Button type="button" loading={confirming} onClick={confirm}><Check size={18} /> Đã cho uống</Button>
          <Button type="button" variant="secondary" disabled={confirming} onClick={onCannot}><XCircle size={18} /> Chưa thể cho uống</Button>
        </div>
      ) : null}
      {error ? <div className="dose-card__error"><ErrorNotice message={error} /></div> : null}
    </article>
  );
}

export function TodayPage({ highlightedId = "", initialDate }: { highlightedId?: string; initialDate?: string }) {
  const [date, setDate] = useState(() => initialDate && isCalendarDate(initialDate) ? initialDate : localDate());
  const [doses, setDoses] = useState<DoseOccurrence[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [cannotDose, setCannotDose] = useState<DoseOccurrence | null>(null);
  const [now, setNow] = useState<number | null>(null);
  const requests = useRef(new RequestSequence());
  const selectedDate = useRef(date);
  const [responseBusy, setResponseBusy] = useState(false);

  useEffect(() => {
    const initialTimer = window.setTimeout(() => setNow(Date.now()), 0);
    const interval = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(interval);
    };
  }, []);

  const load = useCallback(async (quiet = false) => {
    const version = requests.current.start();
    if (!quiet) setLoading(true);
    setError("");
    try {
      const items = await dosesApi.byDate(date);
      if (!requests.current.isCurrent(version)) return;
      setDoses([...items].sort((a, b) => new Date(a.scheduled_for).getTime() - new Date(b.scheduled_for).getTime()));
    } catch (caught) {
      if (requests.current.isCurrent(version)) setError(getErrorMessage(caught));
    } finally {
      if (requests.current.isCurrent(version)) setLoading(false);
    }
  }, [date]);

  useEffect(() => {
    const tracker = requests.current;
    const timer = window.setTimeout(() => { void load(); }, 0);
    const interval = window.setInterval(() => { if (document.visibilityState === "visible") void load(true); }, 30_000);
    const focused = () => { void load(true); };
    window.addEventListener("focus", focused);
    return () => {
      tracker.invalidate();
      window.clearTimeout(timer); window.clearInterval(interval);
      window.removeEventListener("focus", focused);
    };
  }, [load]);

  useEffect(() => {
    if (!initialDate || !isCalendarDate(initialDate)) return;
    const timer = window.setTimeout(() => {
      if (selectedDate.current === initialDate) return;
      selectedDate.current = initialDate;
      requests.current.invalidate();
      setDate(initialDate); setCannotDose(null); setError("");
    }, 0);
    return () => window.clearTimeout(timer);
  }, [initialDate]);

  useEffect(() => {
    if (!highlightedId || loading) return;
    const timeout = window.setTimeout(
      () => document.getElementById(`dose-${highlightedId}`)?.scrollIntoView({ behavior: "smooth", block: "center" }),
      100,
    );
    return () => window.clearTimeout(timeout);
  }, [highlightedId, loading]);

  const dateLabel = useMemo(() => longDate(date), [date]);

  function update(updated: DoseOccurrence) {
    const responseDate = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Ho_Chi_Minh" }).format(new Date(updated.scheduled_for));
    if (responseDate !== selectedDate.current) return;
    requests.current.invalidate();
    setLoading(false);
    setDoses((current) => current.map((dose) => dose.id === updated.id ? updated : dose));
    setCannotDose(current => current?.id === updated.id ? null : current);
  }

  return (
    <>
      <PageHeader eyebrow="Danh sách cần thực hiện" title="Lịch hôm nay" description={`Các lần uống thuốc của ${dateLabel}.`} action={<Button type="button" variant="secondary" onClick={() => { void load(); }} loading={loading}><RefreshCw size={17} /> Làm mới</Button>} />
      <div className="today-toolbar">
        <label className="date-control"><CalendarDays size={18} /><span>Chọn ngày</span><input type="date" disabled={responseBusy} required value={date} onChange={(event) => { const next = event.target.value; if (!isCalendarDate(next) || next === selectedDate.current) return; selectedDate.current = next; requests.current.invalidate(); setLoading(true); setError(""); setCannotDose(null); setDoses([]); setDate(next); }} /></label>
        <p><AlertTriangle size={18} /> “Đã cho uống” là xác nhận của người thao tác. Khi không chắc chắn, hãy kiểm tra trực tiếp.</p>
      </div>
      {error ? <ErrorNotice message={error} /> : null}
      {loading ? <SkeletonList rows={4} /> : doses.length === 0 ? (
        <EmptyState title="Không có lịch thuốc trong ngày" description="Khi có lịch hoạt động, từng lần uống sẽ xuất hiện ở đây để người phụ trách xác nhận." />
      ) : (
        <div className="dose-list">
          {doses.map((dose) => <DoseCard key={dose.id} dose={dose} now={now} highlighted={dose.id === highlightedId} onUpdated={update} onCannot={() => setCannotDose(dose)} />)}
        </div>
      )}
      {cannotDose ? (
        <Modal dismissible={!responseBusy} title="Chưa thể cho uống" description={`${cannotDose.medication_name} · ${cannotDose.elder_name} · ${timeOf(cannotDose.scheduled_for)}`} onClose={() => { if (!responseBusy) setCannotDose(null); }}>
          <CannotAdministerForm dose={cannotDose} onSaved={update} onBusyChange={setResponseBusy} onClose={() => { if (!responseBusy) setCannotDose(null); }} />
        </Modal>
      ) : null}
    </>
  );
}
