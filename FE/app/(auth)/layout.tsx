import Link from "next/link";
import { Brand } from "@/components/brand";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="auth-layout">
      <section className="auth-content">
        <div className="auth-brand"><Brand /></div>
        {children}
        <Link className="auth-back" href="/">← Quay lại trang giới thiệu</Link>
      </section>
    </main>
  );
}

