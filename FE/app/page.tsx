import Link from "next/link";
import { ArrowRight, Camera, ClipboardList, Pill } from "lucide-react";
import { Brand } from "@/components/brand";

export default function LandingPage() {
  return <main className="landing">
    <nav className="landing-nav" aria-label="Điều hướng đầu trang">
      <Brand />
      <div className="landing-nav__actions">
        <Link className="button button--secondary" href="/dang-nhap">Đăng nhập</Link>
        <Link className="button button--primary" href="/dang-ky">Đăng ký</Link>
      </div>
    </nav>
    <section className="landing-content">
      <div className="landing-intro">
        <span className="landing-tag">Chăm sóc tại nhà</span>
        <h1>Việc chăm sóc mỗi ngày, trong một nơi.</h1>
        <p>Theo dõi lịch thuốc, chia sẻ công việc với người chăm sóc và mở công cụ hỗ trợ phát hiện té ngã.</p>
        <Link className="button button--primary" href="/dang-nhap">Mở ứng dụng <ArrowRight size={17} aria-hidden="true" /></Link>
      </div>
      <section className="landing-features" aria-label="Các chức năng HealthGuard">
        <article><span className="feature-icon"><ClipboardList size={22} aria-hidden="true" /></span><h2>Hồ sơ chăm sóc</h2><p>Mời người chăm sóc, phân công và cấp quyền theo từng hồ sơ.</p></article>
        <article><span className="feature-icon feature-icon--blue"><Pill size={22} aria-hidden="true" /></span><h2>Lịch thuốc và OCR</h2><p>Đọc ảnh đơn để điền nháp, xác nhận từng lần uống và nhận nhắc khi đã liên kết Telegram.</p></article>
        <article><span className="feature-icon feature-icon--peach"><Camera size={22} aria-hidden="true" /></span><h2>Phát hiện té ngã</h2><p>Theo dõi qua camera AI, phân tích video và xem cảnh báo FallGuard theo từng hồ sơ.</p></article>
      </section>
    </section>
    <footer className="landing-footer">Hệ thống hỗ trợ theo dõi; không thay thế chỉ định của bác sĩ hoặc dược sĩ.</footer>
  </main>;
}
