import { Heart, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { Brand } from "@/components/brand";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="auth-layout">
      <section className="auth-panel">
        <div className="auth-panel__top"><Brand /></div>
        <div className="auth-panel__message">
          <span className="auth-panel__icon"><Heart size={27} fill="currentColor" aria-hidden="true" /></span>
          <p className="eyebrow">Chăm sóc từ những điều nhỏ</p>
          <h2>Mỗi lần đúng giờ<br />là một lần an tâm.</h2>
          <p>Gia đình cùng người chăm sóc theo dõi lịch thuốc rõ ràng mà không cần nhắn hỏi nhiều lần.</p>
        </div>
        <div className="auth-panel__trust"><ShieldCheck size={19} /><span>Dữ liệu chỉ hiển thị cho thành viên được cấp quyền.</span></div>
      </section>
      <section className="auth-content">
        <div className="auth-content__mobile-brand"><Brand /></div>
        {children}
        <Link className="auth-back" href="/">← Quay lại trang giới thiệu</Link>
      </section>
    </main>
  );
}

