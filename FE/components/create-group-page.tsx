"use client";

import { UsersRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Brand } from "@/components/brand";
import { useAuth } from "@/components/auth-provider";
import { Button, ErrorNotice, Field } from "@/components/ui";
import { authApi, careApi, getErrorMessage } from "@/lib/api";

export function CreateGroupPage() {
  const router = useRouter();
  const { user, setUser } = useAuth();
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const createdHere = useRef(false);

  useEffect(() => {
    if (user?.current_group && !createdHere.current) router.replace("/hom-nay");
  }, [router, user]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (name.trim().length < 2) {
      setError("Tên nhóm cần ít nhất 2 ký tự.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await careApi.createGroup(name.trim());
      const updatedUser = await authApi.me();
      createdHere.current = true;
      setUser(updatedUser);
      router.replace("/nguoi-duoc-cham-soc?welcome=1");
    } catch (caught) {
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
        <form className="form-stack" onSubmit={submit}>
          <Field
            label="Tên nhóm chăm sóc"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Ví dụ: Gia đình anh An"
            autoFocus
            required
          />
          <Button type="submit" loading={saving} className="button--full">
            Tạo nhóm và tiếp tục
          </Button>
        </form>
      </section>
    </main>
  );
}
