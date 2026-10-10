import Link from "next/link";
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
      <h1>Quản lý chăm sóc và lịch thuốc</h1>
      <p>HealthGuard giúp chủ nhóm và người chăm sóc theo dõi hồ sơ, lịch dùng thuốc và kết quả từng lần thực hiện.</p>
      <div className="landing-actions">
        <Link className="button button--primary" href="/dang-nhap">Mở ứng dụng</Link>
        <Link className="button button--secondary" href="/dang-ky">Tạo tài khoản</Link>
      </div>
      <div className="landing-scope">
        <h2>Các chức năng hiện có</h2>
        <ul>
          <li>Quản lý hồ sơ người được chăm sóc.</li>
          <li>Mời người chăm sóc và cấp quyền theo từng hồ sơ.</li>
          <li>Nhập lịch thuốc thủ công hoặc từ ảnh đơn thuốc đã kiểm tra.</li>
          <li>Xác nhận từng lần dùng thuốc và nhận nhắc qua Telegram nếu đã liên kết.</li>
          <li>Chỉnh sửa thông tin tài khoản và đổi mật khẩu.</li>
        </ul>
        <p className="muted">Chủ nhóm tạo nhóm chăm sóc sau khi đăng ký. Người chăm sóc tham gia bằng đường dẫn mời của chủ nhóm.</p>
      </div>
    </section>
    <footer className="landing-footer">Hệ thống hỗ trợ theo dõi; không thay thế chỉ định của bác sĩ hoặc dược sĩ.</footer>
  </main>;
}
