"use client";

import { useState } from "react";
import { Button, ErrorNotice, Field, SuccessNotice } from "@/components/ui";
import { careApi, getErrorMessage } from "@/lib/api";

export function RoomCodePanel() {
  const [password, setPassword] = useState("");
  const [room, setRoom] = useState<{ code: string; expires_at: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function save(revoke = false) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (revoke) {
        await careApi.revokeRoomCode();
        setRoom(null);
        setNotice("Đã thu hồi mã phòng. Thành viên đã tham gia không bị xóa.");
      } else {
        setRoom(await careApi.issueRoomCode(password || undefined));
        setPassword("");
      }
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return <details className="pending-invitations">
    <summary>Mã phòng gia đình</summary>
    <p>Mã có hiệu lực 24 giờ. Chỉ chia sẻ riêng với người tin cậy. Tạo mã mới sẽ hủy mã cũ; mã chỉ hiện tại đây ngay sau khi tạo.</p>
    <form className="form-stack" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <Field label="Mật khẩu phòng (không bắt buộc)" type="password" autoComplete="new-password" minLength={10} maxLength={128} value={password} disabled={busy} onChange={(event) => setPassword(event.target.value)} hint="Nếu đặt, dùng từ 10 ký tự và không dùng lại mật khẩu đăng nhập." />
      <div className="form-actions">
        <Button type="button" variant="secondary" disabled={busy} onClick={() => void save(true)}>Thu hồi mã</Button>
        <Button type="submit" loading={busy}>Tạo mã mới</Button>
      </div>
    </form>
    {error && <ErrorNotice message={error} />}
    {notice && <SuccessNotice message={notice} />}
    {room && <Field label="Mã phòng (sao chép để chia sẻ)" readOnly value={room.code} hint={`Hết hạn: ${new Date(room.expires_at).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" })}`} />}
  </details>;
}
