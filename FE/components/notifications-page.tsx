"use client";

import { BellRing, ExternalLink, RefreshCw, Send, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useAuth } from "@/components/auth-provider";
import { Button, ErrorNotice, PageHeader, SuccessNotice } from "@/components/ui";
import { authApi, getErrorMessage } from "@/lib/api";
import type { TelegramLink } from "@/lib/types";

export function NotificationsPage() {
  const { user, refresh } = useAuth();
  const [link, setLink] = useState<TelegramLink | null>(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [checking, setChecking] = useState(false);

  async function createLink() {
    setCreating(true);
    setError("");
    try {
      setLink(await authApi.createTelegramLink());
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setCreating(false);
    }
  }

  async function checkConnection() {
    setChecking(true);
    setError("");
    try {
      await refresh();
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setChecking(false);
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Kênh nhận nhắc thuốc"
        title="Kết nối thông báo"
        description="Mỗi người liên kết tài khoản Telegram của chính mình để nhận đúng lời nhắc và cảnh báo được phân công."
      />

      {error ? <ErrorNotice message={error} /> : null}
      {user?.telegram_linked ? (
        <SuccessNotice message="Tài khoản này đã được liên kết với Telegram." />
      ) : null}

      <section className="notification-connect-card">
        <span className="notification-connect-card__icon"><Send size={27} aria-hidden="true" /></span>
        <div className="notification-connect-card__content">
          <h2>Telegram Bot</h2>
          <p>
            HealthGuard sẽ gửi giờ uống thuốc, tên người được chăm sóc và nút mở web để xác nhận.
            Mã liên kết chỉ dùng một lần và hết hạn sau 15 phút.
          </p>
          <div className="info-box">
            <ShieldCheck size={18} aria-hidden="true" />
            <p>Chỉ liên kết Telegram của bạn. Không gửi đường dẫn liên kết cho người khác.</p>
          </div>

          <div className="notification-connect-card__actions">
            {!user?.telegram_linked ? (
              <Button type="button" onClick={createLink} loading={creating}>
                <BellRing size={18} aria-hidden="true" /> Tạo liên kết Telegram
              </Button>
            ) : null}
            <Button type="button" variant="secondary" onClick={checkConnection} loading={checking}>
              <RefreshCw size={17} aria-hidden="true" /> Kiểm tra lại trạng thái
            </Button>
          </div>

          {link ? (
            <div className="telegram-link-result" role="status">
              <div>
                <strong>Liên kết đã sẵn sàng</strong>
                <span>Hết hạn lúc {new Intl.DateTimeFormat("vi-VN", { dateStyle: "short", timeStyle: "short" }).format(new Date(link.expires_at))}</span>
              </div>
              <a className="button button--primary" href={link.deep_link} target="_blank" rel="noreferrer">
                Mở Telegram <ExternalLink size={17} aria-hidden="true" />
              </a>
            </div>
          ) : null}
        </div>
      </section>
    </>
  );
}
