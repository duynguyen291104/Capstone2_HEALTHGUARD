"use client";

import { useEffect, useState } from "react";
import { FileScan, Plus, Check } from "lucide-react";
import { Button, ErrorNotice, SuccessNotice } from "@/components/ui";
import { ScheduleForm } from "@/components/medications-page";
import { getErrorMessage, medicationApi } from "@/lib/api";
import type { CareGroupMember, MedicationSchedule } from "@/lib/types";

type Result = Awaited<ReturnType<typeof medicationApi.readPrescription>>;
type Draft = Result["drafts"][number];

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
  const [result, setResult] = useState<Result | null>(null);
  const [selected, setSelected] = useState<{ draft: Draft; index: number } | null>(null);
  const [saved, setSaved] = useState<number[]>([]);
  const [success, setSuccess] = useState("");

  useEffect(() => {
    if (!preview) return;
    return () => URL.revokeObjectURL(preview);
  }, [preview]);

  function selectFile(next: File | null) {
    if (selected && !window.confirm("Đổi ảnh sẽ bỏ bản nháp đang chỉnh. Tiếp tục?")) return;
    setError(""); setSuccess(""); setResult(null); setSelected(null); setSaved([]); setConsent(false); setPreview("");
    if (next && (!['image/jpeg', 'image/png'].includes(next.type) || next.size > 5 * 1024 * 1024 || !next.size)) {
      setFile(null); setError("Hãy chọn ảnh JPG/PNG có dung lượng không quá 5 MB."); return;
    }
    setFile(next);
    if (next) setPreview(URL.createObjectURL(next));
  }

  async function readImage() {
    if (!file || !consent || busy) return;
    setBusy(true); setError("");
    try { setResult(await medicationApi.readPrescription(elderId, file)); }
    catch (caught) { setError(getErrorMessage(caught)); }
    finally { setBusy(false); }
  }

  return <section className="ocr-panel">
    <div className="ocr-panel-heading"><div><h2><FileScan size={22} aria-hidden="true" /> Nhập từ ảnh đơn thuốc</h2><p>Đọc chữ từ ảnh, kiểm tra rồi tạo từng lịch cho {elderName}.</p></div><Button type="button" variant="secondary" onClick={() => setOpen(!open)} aria-expanded={open} aria-controls="prescription-import">{open ? "Thu gọn" : "Tải ảnh đơn thuốc"}</Button></div>
    {open && <div id="prescription-import">
      <label className="field"><span className="field__label">Chọn ảnh đơn thuốc (JPG/PNG, tối đa 5 MB)</span><input type="file" accept="image/jpeg,image/png" disabled={busy || Boolean(selected)} onChange={event => selectFile(event.target.files?.[0] ?? null)} /></label>
      <p className="field__hint">Ưu tiên đơn in rõ nét, chụp ngay ngắn và đủ trang. Ảnh được đọc nội bộ trên máy chủ HealthGuard, không gửi sang dịch vụ OCR bên ngoài và không lưu vào hồ sơ.</p>
      {!result && <><label className="settings-checkbox"><input type="checkbox" checked={consent} disabled={busy} onChange={event => setConsent(event.target.checked)} /> Tôi có quyền sử dụng ảnh và đã kiểm tra đây là đơn thuốc của {elderName}.</label><Button type="button" loading={busy} disabled={!file || !consent} onClick={readImage}><FileScan size={18} aria-hidden="true" /> Đọc đơn thuốc</Button></>}
      {error && <ErrorNotice message={error} />}
      {success && <SuccessNotice message={success} />}
      <div className="ocr-review">
        {preview && <div className="ocr-original"><p>Ảnh gốc — đối chiếu trước khi lưu</p><a href={preview} target="_blank" rel="noreferrer" aria-label="Mở ảnh đơn gốc kích thước đầy đủ">
          {/* Local blob preview: do not upload medical images to an image optimizer. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={preview} alt={`Đơn thuốc đang đối chiếu cho ${elderName}`} />
        </a></div>}
        {result && <div className="ocr-results">
          <div className="info-box"><div>{result.warnings.map(warning => <p key={warning}>{warning}</p>)}</div></div>
          <details open={!result.drafts.length}><summary>Toàn bộ chữ đọc được ({result.provider})</summary><pre className="ocr-text">{result.text}</pre></details>
          {!result.drafts.length && <p>Chưa nhận ra dòng thuốc trong phần chữ bên trên. Thử ảnh rõ hơn, chụp sát trang đơn và ngay ngắn; hoặc dùng “Thêm lịch theo đơn” để nhập từ ảnh gốc.</p>}
          <div className="ocr-drafts">{result.drafts.map((draft, index) => <div key={index} className="ocr-draft"><strong>{draft.medication_name}</strong><Button type="button" variant="secondary" disabled={saved.includes(index) || Boolean(selected)} onClick={() => { setSelected({ draft, index }); setSuccess(""); }}>{saved.includes(index) ? <><Check size={16} /> Đã tạo</> : "Kiểm tra & tạo lịch"}</Button></div>)}</div>
          <Button type="button" variant="secondary" disabled={Boolean(selected)} onClick={() => { setSelected({ draft: { medication_name: "", instructions: "", source_text: "" }, index: -1 }); setSuccess(""); }}><Plus size={17} /> Thêm lịch theo đơn</Button>
          {selected && <section className="ocr-edit"><h3>Kiểm tra và tạo một giờ dùng thuốc</h3><p>Mỗi lần lưu tạo một lịch cho một giờ dùng thuốc. Đối chiếu cả cách dùng (uống, bôi, nhỏ…) và đơn vị với đơn gốc. Nếu đơn có nhiều giờ, dùng “Thêm lịch theo đơn” cho giờ tiếp theo.</p>{selected.draft.source_text && <details><summary>Đoạn chữ nguồn</summary><pre className="ocr-text">{selected.draft.source_text}</pre></details>}
            <ScheduleForm key={selected.index} elderId={elderId} caregivers={caregivers} draft={selected.draft} onClose={() => setSelected(null)} onSaved={schedule => { onSaved(schedule); if (selected.index >= 0) setSaved(current => [...current, selected.index]); setSelected(null); setSuccess(`Đã tạo lịch ${schedule.medication_name} lúc ${schedule.time_of_day.slice(0, 5)}. Kiểm tra các thuốc/giờ còn lại trên đơn.`); }} />
          </section>}
        </div>}
      </div>
    </div>}
  </section>;
}
