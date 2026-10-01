import {
  ArrowRight,
  BellRing,
  Check,
  ClipboardCheck,
  HeartHandshake,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { Brand } from "@/components/brand";

export default function LandingPage() {
  return (
    <main className="landing">
      <nav className="landing-nav" aria-label="Điều hướng đầu trang">
        <Brand />
        <div className="landing-nav__actions">
          <Link className="button button--ghost" href="/dang-nhap">Đăng nhập</Link>
          <Link className="button button--primary" href="/dang-ky">Bắt đầu sử dụng</Link>
        </div>
      </nav>

      <section className="hero">
        <div className="hero__content">
          <p className="hero__tag"><HeartHandshake size={17} aria-hidden="true" /> Đồng hành cùng gia đình mỗi ngày</p>
          <h1>Đúng thuốc. Đúng giờ.<br /><em>An tâm hơn.</em></h1>
          <p className="hero__lead">
            HealthGuard giúp gia đình và người chăm sóc phối hợp lịch dùng thuốc rõ ràng,
            ghi nhận từng lần thực hiện và nhắc nhau khi cần kiểm tra.
          </p>
          <div className="hero__actions">
            <Link className="button button--primary button--large" href="/dang-ky">
              Tạo tài khoản <ArrowRight size={18} aria-hidden="true" />
            </Link>
            <Link className="text-link" href="/dang-nhap">Tôi đã có tài khoản</Link>
          </div>
          <ul className="hero__checks" aria-label="Các ưu điểm chính">
            <li><Check size={16} aria-hidden="true" /> Thiết lập lịch một lần</li>
            <li><Check size={16} aria-hidden="true" /> Phân quyền rõ ràng</li>
            <li><Check size={16} aria-hidden="true" /> Lưu lịch sử minh bạch</li>
          </ul>
        </div>

        <div className="hero-visual" aria-label="Minh họa lịch thuốc hôm nay">
          <div className="hero-orb hero-orb--one" />
          <div className="hero-orb hero-orb--two" />
          <div className="demo-phone">
            <div className="demo-phone__top">
              <div>
                <small>Thứ Hai, 20 tháng 9</small>
                <strong>Chào buổi sáng, anh An</strong>
              </div>
              <span className="avatar">A</span>
            </div>
            <div className="demo-progress">
              <span><strong>2</strong> lần đã xác nhận</span>
              <div><i /></div>
            </div>
            <p className="demo-label">Cần thực hiện tiếp theo</p>
            <div className="demo-dose demo-dose--due">
              <span className="demo-dose__time">08:00</span>
              <span className="demo-dose__icon">Rx</span>
              <span><strong>Metformin 500mg</strong><small>Ông Nguyễn Văn Bình · Sau ăn</small></span>
            </div>
            <div className="demo-confirm"><ClipboardCheck size={18} /> Đã cho uống</div>
            <p className="demo-label">Sắp tới</p>
            <div className="demo-dose">
              <span className="demo-dose__time">12:00</span>
              <span className="demo-dose__icon demo-dose__icon--warm">Rx</span>
              <span><strong>Vitamin D3</strong><small>Bà Trần Thị Mai · Sau ăn</small></span>
            </div>
          </div>
          <div className="floating-note floating-note--top"><BellRing size={20} /><span><strong>Nhắc đúng giờ</strong><small>Không bỏ sót lịch</small></span></div>
          <div className="floating-note floating-note--bottom"><ShieldCheck size={20} /><span><strong>Đã ghi nhận</strong><small>Minh bạch người thao tác</small></span></div>
        </div>
      </section>

      <section className="landing-features" aria-labelledby="features-title">
        <div className="section-heading">
          <p className="eyebrow">Một nơi để cùng phối hợp</p>
          <h2 id="features-title">Nhẹ việc hơn cho cả gia đình</h2>
        </div>
        <div className="feature-grid">
          <article><span><BellRing /></span><h3>Nhắc lịch rõ ràng</h3><p>Người chăm sóc biết chính xác ai cần uống thuốc gì và vào lúc nào.</p></article>
          <article><span><ClipboardCheck /></span><h3>Xác nhận nhanh</h3><p>Mỗi lần dùng thuốc được ghi nhận bằng thao tác đơn giản, dễ kiểm tra lại.</p></article>
          <article><span><ShieldCheck /></span><h3>Quyền riêng biệt</h3><p>Chủ gia đình quyết định người chăm sóc được xem và thao tác trên hồ sơ nào.</p></article>
        </div>
      </section>

      <footer className="landing-footer">
        <Brand />
        <p>Công cụ hỗ trợ theo dõi, không thay thế hướng dẫn của bác sĩ hoặc dược sĩ.</p>
      </footer>
    </main>
  );
}
