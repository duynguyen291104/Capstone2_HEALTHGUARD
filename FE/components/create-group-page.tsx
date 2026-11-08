"use client";

import { UsersRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Brand } from "@/components/brand";
import { useAuth } from "@/components/auth-provider";
import { Button, ErrorNotice, Field } from "@/components/ui";
import { ApiError, authApi, careApi, getErrorMessage } from "@/lib/api";

export function CreateGroupPage() {
  const router = useRouter();
  const { user, setUser } = useAuth();
  const [name, setName] = useState("");
  const [roomCode, setRoomCode] = useState("");
  const [roomPassword, setRoomPassword] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const createdHere = useRef(false);

  useEffect(() => {
    if (user?.current_group && !createdHere.current) router.replace("/hom-nay");
  }, [router, user]);

  async function submit(event: React.FormEvent, join = false) {
    event.preventDefault();
    if (saving) return;
    if (!join && (!name.trim() || name.trim().length > 150)) {
      setError("Tên nhóm cần từ 1 đến 150 ký tự.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const group = join ? await careApi.joinRoom(roomCode.trim(), roomPassword || undefined) : await careApi.createGroup(name.trim());
      createdHere.current = true;
      const membership = { care_group_id: group.id, care_group_name: group.name, role: group.role };
      setUser(user ? { ...user, groups: [membership], current_group: membership } : await authApi.me());
      router.replace(join ? "/hom-nay" : "/nguoi-duoc-cham-soc?welcome=1");
    } catch (caught) {
      if (caught instanceof ApiError && ["NETWORK_ERROR", "GROUP_ALREADY_EXISTS"].includes(caught.code)) {
        try {
          const updated = await authApi.me();
          if (updated.current_group) { createdHere.current = true; setUser(updated); router.replace("/hom-nay"); return; }
        } catch { /* Keep the original useful error if the server is still offline. */ }
      }
      setError(getErrorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="onboarding-page">
      <Brand href="/" />
      <section className="auth-card onboarding-card" aria-labelledby="create-group-title">
        <span className="onboarding-card__icon"><UsersRound size={28} aria-hidden="true" /></span>
        <div className="auth-heading">
          <p className="eyebrow">Bước cuối để bắt đầu</p>
          <h1 id="create-group-title">Tạo nhóm chăm sóc</h1>
          <p>Đây là không gian riêng để bạn thêm bố mẹ, mời người chăm sóc và quản lý lịch thuốc.</p>
        </div>
        {error ? <ErrorNotice message={error} /> : null}
        <form className="form-stack" onSubmit={(event) => void submit(event)}>
          <Field
            label="Tên nhóm chăm sóc"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Ví dụ: Gia đình anh An"
            autoFocus
            required
            maxLength={150}
            disabled={saving}
          />
          <Button type="submit" loading={saving} className="button--full">
            Tạo nhóm và tiếp tục
          </Button>
        </form>
        <hr />
        <form className="form-stack" onSubmit={(event) => void submit(event, true)}>
          <h2>Tham gia bằng mã phòng</h2>
          <Field label="Mã phòng" value={roomCode} onChange={(event) => setRoomCode(event.target.value)} required minLength={32} maxLength={100} disabled={saving} />
          <Field label="Mật khẩu phòng (nếu có)" type="password" autoComplete="off" value={roomPassword} onChange={(event) => setRoomPassword(event.target.value)} minLength={10} maxLength={128} disabled={saving} />
          <Button type="submit" variant="secondary" loading={saving}>Vào nhóm</Button>
        </form>
      </section>
    </main>
  );
}
