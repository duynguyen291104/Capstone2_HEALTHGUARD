"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { BellRing, LockKeyhole, Save, ShieldCheck, UserRound } from "lucide-react";
import { useAuth } from "@/components/auth-provider";
import { Button, ErrorNotice, Field, PageHeader, SuccessNotice } from "@/components/ui";
import { authApi, getErrorMessage } from "@/lib/api";
import { validatePasswordChange } from "@/lib/workflow";

export function AccountSettings() {
  const { user, setUser } = useAuth();
  const [name, setName] = useState(user?.full_name ?? "");
  const [phone, setPhone] = useState(user?.phone ?? "");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [showPasswords, setShowPasswords] = useState(false);
  const [saving, setSaving] = useState(false);
  const [changing, setChanging] = useState(false);
  const [profileError, setProfileError] = useState("");
  const [profileSuccess, setProfileSuccess] = useState("");
  const [passwordError, setPasswordError] = useState("");
  const [passwordSuccess, setPasswordSuccess] = useState("");
  const dirty = name.trim() !== user?.full_name || phone.trim() !== (user?.phone ?? "");

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    setProfileError(""); setProfileSuccess("");
    if (!name.trim()) { setProfileError("Vui lòng nhập họ và tên."); return; }
    if (name.trim().length > 150 || phone.trim().length > 30) { setProfileError("Họ tên tối đa 150 ký tự; số điện thoại tối đa 30 ký tự."); return; }
    setSaving(true);
    try {
      const updated = await authApi.updateProfile({ full_name: name.trim(), phone: phone.trim() || null });
      setUser(updated); setName(updated.full_name); setPhone(updated.phone ?? "");
      setProfileSuccess("Đã lưu thông tin cá nhân.");
    } catch (error) { setProfileError(getErrorMessage(error)); }
    finally { setSaving(false); }
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (changing) return;
    setPasswordError(""); setPasswordSuccess("");
    const validation = validatePasswordChange(currentPassword, newPassword, confirmation);
    if (validation) { setPasswordError(validation); return; }
    setChanging(true);
    try {
      const result = await authApi.changePassword({ current_password: currentPassword, new_password: newPassword });
      setCurrentPassword(""); setNewPassword(""); setConfirmation(""); setShowPasswords(false);
      setPasswordSuccess(result.message);
    } catch (error) { setPasswordError(getErrorMessage(error)); }
    finally { setChanging(false); }
  }

  return <>
    <PageHeader eyebrow="Tài khoản của bạn" title="Cài đặt tài khoản" description="Cập nhật thông tin, bảo vệ tài khoản và giữ kết nối với gia đình." />
    <div className="settings-layout">
      <div className="settings-stack">
        <section className="settings-card" aria-labelledby="profile-heading">
          <header className="settings-heading"><UserRound size={22} aria-hidden="true" /><div><h2 id="profile-heading">Thông tin cá nhân</h2><p>Thông tin để các thành viên nhận biết và liên hệ với bạn.</p></div></header>
          <form onSubmit={saveProfile} className="settings-form">
            {profileError && <ErrorNotice message={profileError} />}
            {profileSuccess && <SuccessNotice message={profileSuccess} />}
            <Field name="full_name" label="Họ và tên" autoComplete="name" required maxLength={150} value={name} disabled={saving} onChange={e => { setName(e.target.value); setProfileSuccess(""); }} />
            <Field name="phone" label="Số điện thoại" type="tel" autoComplete="tel" maxLength={30} value={phone} disabled={saving} onChange={e => { setPhone(e.target.value); setProfileSuccess(""); }} hint="Không bắt buộc. Dùng số mà gia đình có thể liên hệ với bạn." />
            <Field name="email" label="Email đăng nhập" type="email" value={user?.email ?? ""} readOnly hint="Email dùng để đăng nhập và nhận lời mời; hiện chưa hỗ trợ thay đổi." />
            <div className="settings-actions"><Button loading={saving} disabled={!dirty} type="submit"><Save size={17} aria-hidden="true" /> Lưu thay đổi</Button></div>
          </form>
        </section>
        <section className="settings-card" aria-labelledby="password-heading">
          <header className="settings-heading"><LockKeyhole size={22} aria-hidden="true" /><div><h2 id="password-heading">Đổi mật khẩu</h2><p>Sau khi đổi, các thiết bị khác cần đăng nhập lại.</p></div></header>
          <form className="settings-form" onSubmit={changePassword}>
            {passwordError && <ErrorNotice message={passwordError} />}
            {passwordSuccess && <SuccessNotice message={passwordSuccess} />}
            <Field name="current_password" label="Mật khẩu hiện tại" type={showPasswords ? "text" : "password"} autoComplete="current-password" required maxLength={128} value={currentPassword} disabled={changing} onChange={e => { setCurrentPassword(e.target.value); setPasswordSuccess(""); }} />
            <Field name="new_password" label="Mật khẩu mới" type={showPasswords ? "text" : "password"} autoComplete="new-password" required minLength={10} maxLength={128} hint="Từ 10 đến 128 ký tự. Nên kết hợp chữ, số và ký tự đặc biệt." value={newPassword} disabled={changing} onChange={e => setNewPassword(e.target.value)} />
            <Field name="confirm_password" label="Nhập lại mật khẩu mới" type={showPasswords ? "text" : "password"} autoComplete="new-password" required minLength={10} maxLength={128} value={confirmation} disabled={changing} onChange={e => setConfirmation(e.target.value)} />
            <label className="settings-checkbox"><input type="checkbox" checked={showPasswords} onChange={e => setShowPasswords(e.target.checked)} /> Hiện mật khẩu</label>
            <div className="settings-actions"><Button type="submit" loading={changing}><ShieldCheck size={17} aria-hidden="true" /> Cập nhật mật khẩu</Button></div>
          </form>
        </section>
      </div>
      <aside className="settings-stack">
        <section className="settings-card settings-summary">
          <span className="elder-avatar" aria-hidden="true">{user?.full_name.trim().charAt(0).toUpperCase()}</span>
          <h2>{user?.full_name}</h2>
          <p>{user?.email}</p>
          <dl><dt>Nhóm chăm sóc</dt><dd>{user?.current_group?.care_group_name ?? "Chưa tham gia nhóm"}</dd><dt>Vai trò của bạn</dt><dd>{user?.current_group?.role === "OWNER" ? "Chủ gia đình" : "Người chăm sóc"}</dd></dl>
          <p className="settings-note">Quyền chăm sóc được quản lý bởi chủ gia đình trong từng nhóm.</p>
        </section>
        <section className="settings-card">
          <header className="settings-heading"><BellRing size={22} aria-hidden="true" /><div><h2>Thông báo Telegram</h2></div></header>
          <p className="settings-status">{user?.telegram_linked ? "● Đã liên kết Telegram" : "○ Chưa liên kết Telegram"}</p>
          <p className="settings-note">Nhận lời nhắc uống thuốc và thông báo cần xác nhận theo phân công của bạn.</p>
          <Link className="button button--secondary" href="/thong-bao">{user?.telegram_linked ? "Quản lý kết nối" : "Liên kết Telegram"}</Link>
        </section>
      </aside>
    </div>
  </>;
}
