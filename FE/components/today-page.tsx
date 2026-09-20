"use client";

import { AlertTriangle, CalendarDays, Check, CheckCircle2, Clock3, Pill, RefreshCw, XCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, EmptyState, ErrorNotice, Modal, PageHeader, SelectField, SkeletonList, TextareaField } from "@/components/ui";
import { dosesApi, getErrorMessage } from "@/lib/api";
import type { DoseOccurrence, DoseStatus } from "@/lib/types";

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
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Bangkok" }).format(new Date());
}

function longDate(date: string) {
  return new Intl.DateTimeFormat("vi-VN", { dateStyle: "full" }).format(new Date(`${date}T12:00:00`));
}

function timeOf(iso: string) {
  return new Intl.DateTimeFormat("vi-VN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Bangkok" }).format(new Date(iso));
}

function CannotAdministerForm({ dose, onSaved, onClose }: { dose: DoseOccurrence; onSaved: (dose: DoseOccurrence) => void; onClose: () => void }) {
  const [reason, setReason] = useState(reasonOptions[0].value);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      onSaved(await dosesApi.respond(dose.id, { status: "CANNOT_ADMINISTER", reason_code: reason, note: note.trim() || null }));
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="form-stack" onSubmit={submit}>
      {error ? <ErrorNotice message={error} /> : null}
      <SelectField label="Lý do" value={reason} onChange={(event) => setReason(event.target.value)}>
        {reasonOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </SelectField>
      <TextareaField label="Ghi chú thêm" value={note} onChange={(event) => setNote(event.target.value)} placeholder="Thông tin giúp chủ gia đình kiểm tra lại…" />
      <div className="form-actions"><Button type="button" variant="secondary" onClick={onClose}>Hủy</Button><Button type="submit" loading={saving}>Gửi phản hồi</Button></div>
    </form>
  );
}

function DoseCard({ dose, onUpdated, onCannot, highlighted }: { dose: DoseOccurrence; onUpdated: (dose: DoseOccurrence) => void; onCannot: () => void; highlighted?: boolean }) {
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const actionable = dose.status === "DUE" || dose.status === "SCHEDULED";

  async function confirm() {
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
        <div><h2>{dose.medication_name}</h2><p>{dose.dose_amount} {dose.dose_unit} · {dose.elder_name}</p></div>
      </div>
      <div className="dose-card__status">
        {dose.status === "ADMINISTERED" ? <CheckCircle2 size={19} /> : dose.status === "CANNOT_ADMINISTER" || dose.status === "UNCONFIRMED" ? <AlertTriangle size={19} /> : <Clock3 size={19} />}
        <span>
          <strong>{statusLabels[dose.status]}</strong>
          {dose.response?.responded_at ? <small>Lúc {timeOf(dose.response.responded_at)}</small> : dose.status === "UNCONFIRMED" ? <small>Cần liên hệ để kiểm tra</small> : null}
        </span>
      </div>
      {actionable ? (
        <div className="dose-card__actions">
          <Button type="button" loading={confirming} onClick={confirm}><Check size={18} /> Đã cho uống</Button>
          <Button type="button" variant="secondary" onClick={onCannot}><XCircle size={18} /> Chưa thể cho uống</Button>
        </div>
      ) : null}
      {error ? <div className="dose-card__error"><ErrorNotice message={error} /></div> : null}
    </article>
  );
}

export function TodayPage() {
  const [date, setDate] = useState(localDate);
  const [doses, setDoses] = useState<DoseOccurrence[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [cannotDose, setCannotDose] = useState<DoseOccurrence | null>(null);
  const highlightedId = typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("occurrence") ?? "";

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const items = await dosesApi.byDate(date);
      setDoses([...items].sort((a, b) => new Date(a.scheduled_for).getTime() - new Date(b.scheduled_for).getTime()));
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setLoading(false);
    }
  }, [date]);

  useEffect(() => {
    let active = true;
    dosesApi.byDate(date)
      .then((items) => {
        if (!active) return;
        setDoses([...items].sort((a, b) => new Date(a.scheduled_for).getTime() - new Date(b.scheduled_for).getTime()));
      })
      .catch((caught) => { if (active) setError(getErrorMessage(caught)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [date]);

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
    setDoses((current) => current.map((dose) => dose.id === updated.id ? updated : dose));
    setCannotDose(null);
  }

  return (
    <>
      <PageHeader eyebrow="Danh sách cần thực hiện" title="Lịch hôm nay" description={`Các lần uống thuốc của ${dateLabel}.`} action={<Button type="button" variant="secondary" onClick={load} loading={loading}><RefreshCw size={17} /> Làm mới</Button>} />
      <div className="today-toolbar">
        <label className="date-control"><CalendarDays size={18} /><span>Chọn ngày</span><input type="date" value={date} onChange={(event) => { setLoading(true); setDoses([]); setDate(event.target.value); }} /></label>
        <p><AlertTriangle size={18} /> “Đã cho uống” là xác nhận của người thao tác. Khi không chắc chắn, hãy kiểm tra trực tiếp.</p>
      </div>
      {error ? <ErrorNotice message={error} /> : null}
      {loading ? <SkeletonList rows={4} /> : doses.length === 0 ? (
        <EmptyState title="Không có lịch thuốc trong ngày" description="Khi có lịch hoạt động, từng lần uống sẽ xuất hiện ở đây để người phụ trách xác nhận." />
      ) : (
        <div className="dose-list">
          {doses.map((dose) => <DoseCard key={dose.id} dose={dose} highlighted={dose.id === highlightedId} onUpdated={update} onCannot={() => setCannotDose(dose)} />)}
        </div>
      )}
      {cannotDose ? (
        <Modal title="Chưa thể cho uống" description={`${cannotDose.medication_name} · ${cannotDose.elder_name} · ${timeOf(cannotDose.scheduled_for)}`} onClose={() => setCannotDose(null)}>
          <CannotAdministerForm dose={cannotDose} onSaved={update} onClose={() => setCannotDose(null)} />
        </Modal>
      ) : null}
    </>
  );
}
