"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, FileScan, Plus, Trash2 } from "lucide-react";
import { Button, ErrorNotice, Field, SelectField, SuccessNotice, TextareaField } from "@/components/ui";
import { getErrorMessage, medicationApi } from "@/lib/api";
import { createReviewMedication, endDateForDuration, reviewFieldHint, scheduleFromReview, validateReviewMedication, weekDays, type ReviewMedication } from "@/lib/prescription-review";
import type { CareGroupMember, MedicationSchedule, PrescriptionOcrResult } from "@/lib/types";

function today() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Ho_Chi_Minh" }).format(new Date());
}

export function PrescriptionImport({ elderId, elderName, caregivers, onSaved }: {
  elderId: string; elderName: string; caregivers: CareGroupMember[];
  onSaved: (schedule: MedicationSchedule) => void;
}) {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<PrescriptionOcrResult | null>(null);
  const [rows, setRows] = useState<ReviewMedication[]>([]);
  const [startDate, setStartDate] = useState(today);
  const [caregiverId, setCaregiverId] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedSlots, setSavedSlots] = useState<string[]>([]);
  const [success, setSuccess] = useState("");
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const readVersion = useRef(0);
  const saveInProgress = useRef(false);

  useEffect(() => {
    if (!preview) return;
    return () => URL.revokeObjectURL(preview);
  }, [preview]);

  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(() => setElapsedSeconds(value => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);

  useEffect(() => () => { readVersion.current += 1; }, []);

  function selectFile(next: File | null) {
    if (rows.length && !window.confirm("Đổi ảnh sẽ bỏ các bản nháp chưa lưu. Tiếp tục?")) return;
    readVersion.current += 1;
    setError(""); setSuccess(""); setResult(null); setRows([]); setSavedSlots([]); setReviewed(false); setConsent(false); setPreview("");
    if (next && (!['image/jpeg', 'image/png'].includes(next.type) || next.size > 5 * 1024 * 1024 || !next.size)) {
      setFile(null); setError("Hãy chọn ảnh JPG/PNG có dung lượng không quá 5 MB."); return;
    }
    setFile(next);
    if (next) setPreview(URL.createObjectURL(next));
  }

  async function readImage() {
    if (!file || !consent || busy) return;
    const version = ++readVersion.current;
    setBusy(true); setError(""); setElapsedSeconds(0);
    try {
      const next = await medicationApi.readPrescription(elderId, file);
      if (version !== readVersion.current) return;
      setResult(next);
      setRows(next.drafts.map((draft, index) => createReviewMedication(draft, `ocr-${index}`)));
      setReviewed(false);
    } catch (caught) {
      if (version === readVersion.current) setError(getErrorMessage(caught));
    } finally {
      if (version === readVersion.current) setBusy(false);
    }
  }

  function updateRow(id: string, values: Partial<ReviewMedication>, editedField?: string) {
    setRows(current => current.map(row => row.id === id ? {
      ...row, ...values,
      editedFields: editedField ? [...new Set([...row.editedFields, editedField])] : row.editedFields,
    } : row));
    setReviewed(false); setError(""); setSuccess("");
  }

  function startManualEntry() {
    if (busy || saving || savedSlots.length) return;
    if (rows.length && !window.confirm("Bỏ các gợi ý OCR chưa lưu và nhập tay từ ảnh gốc?")) return;
    setRows([createReviewMedication(null, `manual-${Date.now()}`)]);
    setResult(current => ({ provider: "Nhập thủ công", text: current?.text ?? "", drafts: [], warnings: [], quality: { recognized_medications: 0, prefilled_fields: 0, total_fields: 0, coverage_percent: 0, low_confidence_fields: 0, elapsed_ms: 0, preprocessing: [] } }));
    setReviewed(false); setError(""); setSuccess("");
  }

  const reviewHint = reviewFieldHint;

  async function saveAll(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saveInProgress.current || !reviewed) return;
    const selected = rows.filter(row => row.included);
    if (!selected.length) { setError("Chọn ít nhất một thuốc để tạo lịch."); return; }
    for (const row of selected) {
      const validation = validateReviewMedication(row, startDate);
      if (validation) {
        setError(`${row.medicationName || "Thuốc chưa có tên"}: ${validation}`);
        document.getElementById(`review-${row.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
        return;
      }
    }
    saveInProgress.current = true;
    setSaving(true); setError(""); setSuccess("");
    let created = 0;
    try {
      for (const row of selected) {
        for (const slot of row.administrations) {
          if (savedSlots.includes(slot.id)) continue;
          const schedule = await medicationApi.create(elderId, scheduleFromReview(row, slot, startDate, caregiverId), slot.requestKey);
          onSaved(schedule);
          setSavedSlots(current => [...new Set([...current, slot.id])]);
          created += 1;
        }
      }
      setSuccess(`Đã tạo ${created} lịch thuốc. Các lần dùng đã được chuyển sang hệ thống nhắc thuốc.`);
      setReviewed(false);
    } catch (caught) {
      setError(`${getErrorMessage(caught)} ${created ? `Đã lưu ${created} lịch trong lần này. ` : ""}Các lịch đã lưu được giữ lại; bấm lưu lại chỉ gửi các lịch còn thiếu.`);
    } finally {
      saveInProgress.current = false;
      setSaving(false);
    }
  }

  const totalSlots = rows.filter(row => row.included).reduce((count, row) => count + row.administrations.length, 0);
  const pendingSlots = rows.filter(row => row.included).reduce((count, row) => count + row.administrations.filter(slot => !savedSlots.includes(slot.id)).length, 0);

  return <section className="ocr-panel">
    <div className="ocr-panel-heading"><div><h2><FileScan size={22} aria-hidden="true" /> Nhập từ ảnh đơn thuốc</h2><p>Điền sẵn thông tin đọc được, sửa các ô vàng rồi lưu lịch cho {elderName}.</p></div><Button type="button" variant="secondary" disabled={saving} onClick={() => setOpen(!open)} aria-expanded={open} aria-controls="prescription-import">{open ? "Thu gọn" : "Tải ảnh đơn thuốc"}</Button></div>
    {open && <div id="prescription-import">
      <label className="field"><span className="field__label">Chọn ảnh đơn thuốc (JPG/PNG, tối đa 5 MB)</span><input type="file" accept="image/jpeg,image/png" disabled={busy || saving} onChange={event => selectFile(event.target.files?.[0] ?? null)} /></label>
      <p className="field__hint">Chụp sát một trang, đủ sáng và tránh lóa. Ảnh được xử lý nội bộ, không lưu vào hồ sơ.</p>
      {!result && <><label className="settings-checkbox"><input type="checkbox" checked={consent} disabled={busy} onChange={event => setConsent(event.target.checked)} /> Tôi có quyền sử dụng ảnh và đã kiểm tra đây là đơn thuốc của {elderName}.</label><div className="ocr-read-actions"><Button type="button" loading={busy} disabled={!file || !consent} onClick={readImage}><FileScan size={18} aria-hidden="true" /> {busy ? "Đang đọc đơn thuốc…" : "Đọc và điền form"}</Button><Button type="button" variant="ghost" disabled={busy} onClick={startManualEntry}>Nhập từ ảnh bằng tay</Button></div></>}
      {busy && <p className="ocr-progress" role="status">Đang căn ảnh và đọc chữ · {elapsedSeconds}s. Bạn không cần tải lại ảnh.</p>}
      {error && <ErrorNotice message={error} />}
      {success && <SuccessNotice message={success} />}
      <div className={`ocr-review ${!preview ? "ocr-review--no-preview" : ""}`}>
        {preview && <div className="ocr-original"><p>Ảnh gốc — bấm để xem lớn</p><a href={preview} target="_blank" rel="noreferrer" aria-label="Mở ảnh đơn gốc kích thước đầy đủ">
          {/* Blob preview stays local; never send medical photos to an image optimizer. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={preview} alt={`Đơn thuốc đang đối chiếu cho ${elderName}`} />
        </a></div>}
        {result && <div className="ocr-results">
          {result.quality?.total_fields > 0 && <div className="ocr-quality" role="status"><strong>{result.drafts.length} thuốc · điền sẵn {result.quality.prefilled_fields}/{result.quality.total_fields} trường ({result.quality.coverage_percent}%)</strong><span>Đây là mức điền sẵn, không phải tỷ lệ đọc đúng. Ô vàng cần kiểm tra hoặc bổ sung.</span>{result.quality.coverage_percent < 70 && <span className="ocr-quality-warning">Ảnh này còn nhiều trường thiếu. Bạn có thể sửa ngay bên dưới hoặc nhập thủ công, không cần chờ đọc lại.</span>}</div>}
          {result.provider === "Nhập thủ công" ? <p>Đang nhập tay từ ảnh gốc. Chữ OCR (nếu có) vẫn được giữ bên dưới để tham khảo.</p> : <Button type="button" variant="ghost" disabled={saving || savedSlots.length > 0} onClick={startManualEntry}>Bỏ bản OCR và nhập tay</Button>}
          {result.warnings.length > 0 && <details className="ocr-notes"><summary>Lưu ý khi đối chiếu</summary>{result.warnings.map(warning => <p key={warning}>{warning}</p>)}</details>}
          {result.text && <details open={result.provider !== "Nhập thủ công" && !result.drafts.length}><summary>Toàn bộ chữ đọc được</summary><pre className="ocr-text">{result.text}</pre></details>}
          {!rows.length && <p>Chưa tách được thuốc. Bấm “Thêm thuốc” để nhập trực tiếp từ ảnh gốc, hoặc chọn ảnh rõ hơn.</p>}
          <form onSubmit={saveAll} className="ocr-batch-form">
            {rows.length > 0 && <div className="ocr-shared-settings"><h3>Thiết lập lịch do bạn chọn</h3><p>Ngày bắt đầu và người phụ trách áp dụng cho các thuốc đang chọn.</p><div className="field-pair"><Field label="Ngày bắt đầu *" name="ocr-start-date" type="date" required value={startDate} disabled={saving || savedSlots.length > 0} onChange={event => { setStartDate(event.target.value); setReviewed(false); }} /><SelectField label="Người phụ trách" value={caregiverId} disabled={saving || savedSlots.length > 0} onChange={event => { setCaregiverId(event.target.value); setReviewed(false); }}><option value="">Chưa chỉ định</option>{caregivers.map(caregiver => <option key={caregiver.user_id} value={caregiver.user_id}>{caregiver.full_name}</option>)}</SelectField></div></div>}
            {rows.map((row, index) => {
              const hasSaved = row.administrations.some(slot => savedSlots.includes(slot.id));
              const allSaved = row.administrations.every(slot => savedSlots.includes(slot.id));
              const nameHint = reviewHint(row, "medication_name", !row.medicationName);
              const instructionsHint = reviewHint(row, "instructions", !row.instructions);
              const durationHint = reviewHint(row, "duration_days", !row.durationDays && !row.endDate);
              const daysHint = reviewHint(row, "days_of_week", !row.days.length);
              return <section key={row.id} id={`review-${row.id}`} className={`ocr-medication-card ${!row.included ? "ocr-medication-card--excluded" : ""}`}>
                <header className="ocr-card-heading"><label className="ocr-include"><input type="checkbox" checked={row.included} disabled={saving || hasSaved} onChange={event => updateRow(row.id, { included: event.target.checked })} /><strong>Thuốc {index + 1}</strong></label>{allSaved ? <span className="ocr-saved"><Check size={16} /> Đã tạo lịch</span> : row.source?.confidence != null && row.source.confidence < 80 ? <span className="ocr-review-tag"><AlertTriangle size={15} /> Có chữ đọc chưa rõ</span> : <span className="field__hint">Đối chiếu với đơn gốc</span>}</header>
                <fieldset disabled={saving || !row.included} className="ocr-card-fields">
                  {row.source?.field_reviews?.filter(item => item.field === "administrations").map((item, reviewIndex) => <p key={reviewIndex} className="ocr-quality-warning"><AlertTriangle size={16} aria-hidden="true" /> {item.reason} Kiểm tra số lần dùng bên dưới; thêm hoặc xóa lần dùng nếu cần.</p>)}
                  <Field name={`ocr-name-${row.id}`} label="Tên thuốc và hàm lượng *" maxLength={200} value={row.medicationName} disabled={hasSaved} className={nameHint ? "ocr-field--review" : ""} hint={nameHint} onChange={event => updateRow(row.id, { medicationName: event.target.value }, "medication_name")} />
                  <TextareaField id={`ocr-instructions-${row.id}`} label="Cách dùng theo đơn" maxLength={3000} rows={2} value={row.instructions} disabled={hasSaved} className={instructionsHint ? "ocr-field--review" : ""} hint={instructionsHint} onChange={event => updateRow(row.id, { instructions: event.target.value }, "instructions")} />
                  <div className="ocr-administrations">{row.administrations.map(slot => {
                    const slotSaved = savedSlots.includes(slot.id);
                    const amountHint = reviewHint(row, `slot-${slot.id}:dose_amount`, !slot.doseAmount);
                    const unitHint = reviewHint(row, `slot-${slot.id}:dose_unit`, !slot.doseUnit);
                    const timeHint = reviewHint(row, `slot-${slot.id}:time_of_day`, !slot.time);
                    function updateSlot(values: Partial<typeof slot>, field: string) {
                      updateRow(row.id, { administrations: row.administrations.map(item => item.id === slot.id ? { ...item, ...values } : item) }, `slot-${slot.id}:${field}`);
                    }
                    return <div key={slot.id} className="ocr-dose-row"><div className="ocr-dose-heading"><strong>{slot.label}</strong>{slotSaved ? <span className="ocr-saved"><Check size={14} /> Đã lưu</span> : row.administrations.length > 1 && <button type="button" className="icon-button" aria-label={`Xóa ${slot.label}`} disabled={hasSaved} onClick={() => updateRow(row.id, { administrations: row.administrations.filter(item => item.id !== slot.id) })}><Trash2 size={16} /></button>}</div><div className="ocr-dose-fields"><Field name={`amount-${slot.id}`} label="Liều mỗi lần *" type="number" min="0.01" step="0.01" required value={slot.doseAmount} disabled={slotSaved} className={amountHint ? "ocr-field--review" : ""} hint={amountHint} onChange={event => updateSlot({ doseAmount: event.target.value }, "dose_amount")} /><Field name={`unit-${slot.id}`} label="Đơn vị *" required maxLength={50} placeholder="viên, ml, ống…" value={slot.doseUnit} disabled={slotSaved} className={unitHint ? "ocr-field--review" : ""} hint={unitHint} onChange={event => updateSlot({ doseUnit: event.target.value }, "dose_unit")} /><Field name={`time-${slot.id}`} label="Giờ dùng *" type="time" required value={slot.time} disabled={slotSaved} className={timeHint ? "ocr-field--review" : ""} hint={timeHint ?? (!slot.time ? `Chọn giờ cụ thể cho ${slot.label.toLowerCase()}.` : undefined)} onChange={event => updateSlot({ time: event.target.value }, "time_of_day")} /></div></div>;
                  })}</div>
                  {!hasSaved && <Button type="button" variant="ghost" onClick={() => updateRow(row.id, { administrations: [...row.administrations, { id: `${row.id}-${Date.now()}`, requestKey: crypto.randomUUID(), label: `Lần dùng ${row.administrations.length + 1}`, doseAmount: row.administrations[0]?.doseAmount ?? "", doseUnit: row.administrations[0]?.doseUnit ?? "", time: "" }] })}><Plus size={15} /> Thêm giờ dùng cho thuốc này</Button>}
                  <fieldset className={`ocr-weekdays ${daysHint ? "ocr-field--review" : ""}`} disabled={hasSaved}><legend>Ngày dùng trong tuần *</legend><div className="weekday-picker">{weekDays.map((day, dayIndex) => <label key={dayIndex}><input type="checkbox" checked={row.days.includes(dayIndex)} onChange={() => updateRow(row.id, { days: row.days.includes(dayIndex) ? row.days.filter(value => value !== dayIndex) : [...row.days, dayIndex].sort() }, "days_of_week")} /><span>{day}</span></label>)}</div><button type="button" className="ocr-daily-button" onClick={() => updateRow(row.id, { days: [0, 1, 2, 3, 4, 5, 6] }, "days_of_week")}>Chọn hằng ngày</button>{daysHint && <span className="field__hint">{daysHint}</span>}</fieldset>
                  <div className="field-pair"><Field name={`duration-${row.id}`} label="Số ngày dùng theo đơn" type="number" min="1" max="3650" step="1" value={row.durationDays} disabled={hasSaved} className={durationHint ? "ocr-field--review" : ""} hint={durationHint} onChange={event => updateRow(row.id, { durationDays: event.target.value, endDate: "" }, "duration_days")} /><Field name={`end-${row.id}`} label="Ngày kết thúc" type="date" min={startDate} value={row.endDate || endDateForDuration(startDate, row.durationDays)} disabled={hasSaved} hint="Để trống chỉ khi đơn cho phép dùng đến chỉ định mới." onChange={event => updateRow(row.id, { endDate: event.target.value, durationDays: "" }, "duration_days")} /></div>
                  {row.source?.source_text && <details><summary>Chữ nguồn của thuốc này</summary><pre className="ocr-text">{row.source.source_text}</pre></details>}
                </fieldset>
              </section>;
            })}
            <Button type="button" variant="secondary" disabled={saving} onClick={() => { setRows(current => [...current, createReviewMedication(null, `manual-${Date.now()}`)]); setReviewed(false); }}><Plus size={17} /> Thêm thuốc</Button>
            {rows.length > 0 && <div className="ocr-save-bar"><p>{savedSlots.length > 0 ? `Đã lưu ${savedSlots.length} lịch. ` : ""}{pendingSlots} lịch còn chờ xác nhận. Nhắc đúng giờ, sau 15/30/45 phút; báo chủ nhóm sau 60 phút chưa phản hồi.</p><label className="settings-checkbox"><input type="checkbox" checked={reviewed} disabled={saving || !pendingSlots} onChange={event => setReviewed(event.target.checked)} /> Tôi đã đối chiếu đúng người, tên thuốc, liều, cách dùng, ngày và giờ với đơn gốc; đã kiểm tra các ô vàng.</label><Button type="submit" loading={saving} disabled={!reviewed || !pendingSlots || !totalSlots}><Check size={17} /> {saving ? "Đang lưu các lịch…" : !pendingSlots && savedSlots.length ? "Đã tạo các lịch đã chọn" : `Tạo ${pendingSlots} lịch đã kiểm tra`}</Button></div>}
          </form>
        </div>}
      </div>
    </div>}
  </section>;
}
