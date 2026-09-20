# HealthGuard Web

Giao diện Next.js cho module hồ sơ người cao tuổi, phân quyền người chăm sóc và lịch uống thuốc.

## Yêu cầu

- Node.js 20.9 trở lên (máy hiện tại đang dùng Node.js 24).
- Backend FastAPI chạy tại `http://localhost:8000`.

## Chạy trên máy cá nhân

```bash
npm install
copy .env.example .env.local
npm run dev
```

Mở `http://localhost:3000`.

Biến môi trường:

```text
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
```

Frontend gửi cookie phiên với `credentials: "include"` và tự gắn `X-Care-Group-ID` cho các API thuộc nhóm chăm sóc hiện tại.

## Các màn hình

- `/`: trang giới thiệu.
- `/dang-ky`: đăng ký tài khoản chủ gia đình và tạo nhóm đầu tiên.
- `/dang-nhap`: đăng nhập.
- `/tham-gia?token=...`: đăng ký người chăm sóc bằng lời mời.
- `/hom-nay`: các lần uống thuốc cần xử lý.
- `/nguoi-duoc-cham-soc`: quản lý hồ sơ người cao tuổi.
- `/nguoi-cham-soc`: mời, phân công và cấp quyền cho người chăm sóc.
- `/lich-thuoc`: thiết lập và ngừng lịch thuốc.

Deep link từ Telegram dùng `/hom-nay?occurrence=<uuid>` để tự cuộn đến lần uống tương ứng.

## Kiểm tra trước khi ghép nhánh

```bash
npm run lint
npm run typecheck
npm run build
```

