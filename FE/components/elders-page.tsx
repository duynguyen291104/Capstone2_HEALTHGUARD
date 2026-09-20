"use client";

import { CalendarDays, HeartPulse, Pencil, Phone, Plus, Ruler, Weight } from "lucide-react";
import { useEffect, useState } from "react";
import { useAuth } from "@/components/auth-provider";
import {
  Button,
  EmptyState,
  ErrorNotice,
  Field,
  Modal,
  PageHeader,
  SelectField,
  SkeletonList,
  TextareaField,
} from "@/components/ui";
import { eldersApi, getErrorMessage } from "@/lib/api";
import type { Elder } from "@/lib/types";

const emptyForm = {
  full_name: "",
  date_of_birth: "",
  sex: "",
  height_cm: "",
  weight_kg: "",
  diagnosed_conditions: "",
  current_medications_note: "",
  mobility_level: "",
  sleep_habits: "",
  emergency_contact_name: "",
  emergency_contact_phone: "",
};

type ElderFormState = typeof emptyForm;

function toForm(elder?: Elder | null): ElderFormState {
  if (!elder) return emptyForm;
  return {
    full_name: elder.full_name,
    date_of_birth: elder.date_of_birth ?? "",
    sex: elder.sex ?? "",
    height_cm: elder.height_cm?.toString() ?? "",
    weight_kg: elder.weight_kg?.toString() ?? "",
    diagnosed_conditions: elder.diagnosed_conditions?.join(", ") ?? "",
    current_medications_note: elder.current_medications_note ?? "",
    mobility_level: elder.mobility_level ?? "",
    sleep_habits: elder.sleep_habits ?? "",
    emergency_contact_name: elder.emergency_contact_name ?? "",
    emergency_contact_phone: elder.emergency_contact_phone ?? "",
  };
}

function nullableNumber(value: string) {
  return value ? Number(value) : null;
}

function nullable(value: string) {
  return value.trim() || null;
}

function birthYear(date: string | null) {
  if (!date) return "Chưa có ngày sinh";
  const year = new Date(date).getFullYear();
  return Number.isNaN(year) ? "Chưa có ngày sinh" : `Sinh năm ${year}`;
}

function ElderForm({ elder, onSaved, onClose }: { elder?: Elder | null; onSaved: (elder: Elder) => void; onClose: () => void }) {
  const [values, setValues] = useState<ElderFormState>(() => toForm(elder));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  function update(name: keyof ElderFormState, value: string) {
    setValues((current) => ({ ...current, [name]: value }));
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!values.full_name.trim()) return setError("Hãy nhập họ tên người được chăm sóc.");
    setSaving(true);
    setError("");
    const body = {
      full_name: values.full_name.trim(),
      date_of_birth: nullable(values.date_of_birth),
      sex: values.sex || "UNDISCLOSED",
      height_cm: nullableNumber(values.height_cm),
      weight_kg: nullableNumber(values.weight_kg),
      diagnosed_conditions: values.diagnosed_conditions.split(/[,\n]/).map((item) => item.trim()).filter(Boolean),
      current_medications_note: nullable(values.current_medications_note),
      mobility_level: nullable(values.mobility_level),
      sleep_habits: nullable(values.sleep_habits),
      emergency_contact_name: nullable(values.emergency_contact_name),
      emergency_contact_phone: nullable(values.emergency_contact_phone),
    };
    try {
      const saved = elder ? await eldersApi.update(elder.id, body) : await eldersApi.create(body);
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
      <Field label="Họ và tên *" value={values.full_name} onChange={(event) => update("full_name", event.target.value)} required />
      <Field label="Ngày sinh" type="date" value={values.date_of_birth} onChange={(event) => update("date_of_birth", event.target.value)} />
      <SelectField label="Giới tính" value={values.sex} onChange={(event) => update("sex", event.target.value)}>
        <option value="">Chưa khai báo</option>
        <option value="MALE">Nam</option>
        <option value="FEMALE">Nữ</option>
        <option value="OTHER">Khác</option>
      </SelectField>
      <SelectField label="Khả năng đi lại" value={values.mobility_level} onChange={(event) => update("mobility_level", event.target.value)}>
        <option value="">Chưa khai báo</option>
        <option value="INDEPENDENT">Tự đi lại</option>
        <option value="NEEDS_ASSISTANCE">Cần người hỗ trợ</option>
        <option value="WHEELCHAIR">Dùng xe lăn</option>
        <option value="BEDRIDDEN">Nằm tại giường</option>
      </SelectField>
      <Field label="Chiều cao (cm)" type="number" min="30" max="250" step="0.1" value={values.height_cm} onChange={(event) => update("height_cm", event.target.value)} />
      <Field label="Cân nặng (kg)" type="number" min="1" max="400" step="0.1" value={values.weight_kg} onChange={(event) => update("weight_kg", event.target.value)} />
      <TextareaField label="Bệnh đã được chẩn đoán" hint="Ngăn cách bằng dấu phẩy, theo thông tin từ cơ sở y tế." value={values.diagnosed_conditions} onChange={(event) => update("diagnosed_conditions", event.target.value)} />
      <TextareaField label="Thuốc đang sử dụng (ghi chú)" value={values.current_medications_note} onChange={(event) => update("current_medications_note", event.target.value)} />
      <TextareaField label="Thói quen ngủ" value={values.sleep_habits} onChange={(event) => update("sleep_habits", event.target.value)} />
      <div className="field-pair">
        <Field label="Người liên hệ khẩn cấp" value={values.emergency_contact_name} onChange={(event) => update("emergency_contact_name", event.target.value)} />
        <Field label="Số điện thoại" type="tel" value={values.emergency_contact_phone} onChange={(event) => update("emergency_contact_phone", event.target.value)} />
      </div>
      <div className="form-actions form-grid__full">
        <Button type="button" variant="secondary" onClick={onClose}>Hủy</Button>
        <Button type="submit" loading={saving}>{elder ? "Lưu thay đổi" : "Thêm hồ sơ"}</Button>
      </div>
    </form>
  );
}

export function EldersPage() {
  const { user } = useAuth();
  const [elders, setElders] = useState<Elder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Elder | "new" | null>(null);
  const isOwner = user?.current_group?.role === "OWNER";

  useEffect(() => {
    let active = true;
    eldersApi.list()
      .then((items) => { if (active) setElders(items); })
      .catch((caught) => { if (active) setError(getErrorMessage(caught)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  function saved(elder: Elder) {
    setElders((current) => current.some((item) => item.id === elder.id)
      ? current.map((item) => item.id === elder.id ? elder : item)
      : [elder, ...current]);
    setEditing(null);
  }

  return (
    <>
      <PageHeader
        eyebrow="Hồ sơ chăm sóc"
        title="Người được chăm sóc"
        description={isOwner ? "Lưu thông tin nền để cả gia đình phối hợp đúng người, đúng nhu cầu." : "Bạn chỉ thấy những hồ sơ được chủ gia đình phân công."}
        action={isOwner ? <Button onClick={() => setEditing("new")}><Plus size={18} /> Thêm hồ sơ</Button> : undefined}
      />
      {error ? <ErrorNotice message={error} /> : null}
      {loading ? <SkeletonList /> : elders.length === 0 ? (
        <EmptyState
          title={isOwner ? "Chưa có hồ sơ nào" : "Bạn chưa được phân công"}
          description={isOwner ? "Thêm bố, mẹ hoặc người thân cần chăm sóc để bắt đầu tạo lịch thuốc." : "Hãy nhờ chủ gia đình phân công một người được chăm sóc cho bạn."}
          action={isOwner ? <Button onClick={() => setEditing("new")}><Plus size={18} /> Thêm người đầu tiên</Button> : undefined}
        />
      ) : (
        <div className="elder-grid">
          {elders.map((elder) => (
            <article className="elder-card" key={elder.id}>
              <div className="elder-card__top">
                <span className="elder-avatar">{elder.full_name.charAt(0).toUpperCase()}</span>
                <div><h2>{elder.full_name}</h2><p>{birthYear(elder.date_of_birth)}</p></div>
                {isOwner ? <button className="icon-button" type="button" aria-label={`Sửa hồ sơ ${elder.full_name}`} onClick={() => setEditing(elder)}><Pencil size={18} /></button> : null}
              </div>
              <div className="elder-card__metrics">
                <span><Ruler size={17} /> {elder.height_cm ? `${elder.height_cm} cm` : "—"}</span>
                <span><Weight size={17} /> {elder.weight_kg ? `${elder.weight_kg} kg` : "—"}</span>
                <span><CalendarDays size={17} /> {elder.mobility_level ? "Đã cập nhật đi lại" : "Chưa có thông tin đi lại"}</span>
              </div>
              <div className="elder-card__section">
                <p><HeartPulse size={16} /> Bệnh đã chẩn đoán</p>
                <div className="tag-list">
                  {elder.diagnosed_conditions?.length ? elder.diagnosed_conditions.map((condition) => <span key={condition}>{condition}</span>) : <em>Chưa khai báo hoặc bạn chưa được cấp quyền xem</em>}
                </div>
              </div>
              <div className="elder-card__contact"><Phone size={17} /><span><small>Liên hệ khẩn cấp</small><strong>{elder.emergency_contact_name || "Chưa khai báo"}{elder.emergency_contact_phone ? ` · ${elder.emergency_contact_phone}` : ""}</strong></span></div>
            </article>
          ))}
        </div>
      )}
      {editing ? (
        <Modal title={editing === "new" ? "Thêm người được chăm sóc" : "Cập nhật hồ sơ"} description="Chỉ nhập thông tin đã được xác nhận; bạn có thể bổ sung sau." onClose={() => setEditing(null)}>
          <ElderForm elder={editing === "new" ? null : editing} onSaved={saved} onClose={() => setEditing(null)} />
        </Modal>
      ) : null}
    </>
  );
}
