# Capstone2 HEALTHGUARD

Repository chung của nhóm HEALTHGUARD. Nhánh này triển khai module quản lý chăm sóc và nhắc uống thuốc cho người cao tuổi; thư mục `AI` dành cho phần phát hiện té ngã của thành viên khác.

## Phạm vi của module này

- Đăng ký, đăng nhập, đăng xuất và phiên đăng nhập có thể thu hồi.
- Hai vai trò theo từng nhóm gia đình: `OWNER` và `CAREGIVER`.
- Chủ nhà mời, phân công hoặc thu hồi người chăm sóc.
- Hồ sơ người được chăm sóc.
- Thuốc, lịch uống và các lần uống theo ngày.
- Xác nhận `Đã cho uống` hoặc `Chưa thể cho uống`.
- Worker nhắc tối đa theo cấu hình và chuyển sang `UNCONFIRMED` khi hết thời gian.
- Telegram chỉ gửi thông báo cho chủ nhóm; người chăm sóc xem lịch và xác nhận trên web theo phân công.
- Nhập đơn thuốc từ ảnh bằng OpenCV + Tesseract nội bộ, sửa bản nháp và xác nhận trước khi lưu.
- Cài đặt tài khoản: sửa thông tin cá nhân và đổi mật khẩu, thu hồi các phiên cũ.

`UNCONFIRMED` chỉ có nghĩa là chưa có xác nhận. Hệ thống không tự kết luận người cao tuổi đã bỏ thuốc, không chẩn đoán và không hướng dẫn thay đổi liều.

## Kiến trúc

```text
FE (Next.js) ──HTTP + HttpOnly cookie──> BE (FastAPI)
                                              │
                                              ├── PostgreSQL
                                              ├── Medication worker
                                              └── Telegram Bot API (tùy chọn)
```

| Thư mục | Nội dung |
|---|---|
| `FE` | Web Next.js bằng tiếng Việt |
| `BE` | API FastAPI, migration Alembic, worker và test |
| `AI` | Phần AI té ngã của nhóm, nhánh này không sửa |
| `docs` | Phạm vi, hợp đồng API và tiêu chí nghiệm thu |

## Yêu cầu trên máy

- PostgreSQL đang chạy ở cổng `5432`.
- Database `eldercare_dev` và login role `eldercare_app`.
- Python 3.12 trở lên.
- Node.js 20.9 trở lên và npm.
- Tesseract với ngôn ngữ Việt/Anh để đọc đơn thuốc; dùng `setup-ocr.cmd` theo [hướng dẫn OCR](docs/OCR_REVIEW_GUIDE.md). OpenCV được cài cùng dependencies Python.

Docker không bắt buộc cho môi trường phát triển hiện tại.

## Chạy nhanh trên máy đã cấu hình

Tại thư mục gốc, chỉ cần một cửa sổ PowerShell:

```powershell
.\run-healthguard.cmd
```

Mở `http://localhost:3000`. Lệnh này chạy API, worker và frontend; tự thử Telegram nếu đã có cấu hình. Lỗi tunnel/Telegram không làm tắt các chức năng web. Giữ cửa sổ mở, nhấn Ctrl+C để dừng các tiến trình do launcher tạo. Nếu cổng đã có ứng dụng chạy, launcher báo lỗi; không tự tắt tiến trình khác.

Khi demo không cần Telegram hoặc không có mạng:

```powershell
.\run-healthguard.cmd --local-only
```

Chế độ này không sửa `.env`; chỉ tắt Telegram trong lượt chạy đó. PostgreSQL vẫn phải hoạt động. Các hướng dẫn tách terminal bên dưới dành cho cài đặt mới hoặc phát triển từng thành phần.

Để chạy cả camera AI và phân tích video trong HealthGuard, dùng `./run-healthguard.cmd --with-ai` sau khi cập nhật database và chuẩn bị môi trường AI. Xem [cách chạy FallGuard và dùng chung bot Telegram](FE/FALLGUARD.md).

Xem [checklist kiểm thử và bảo vệ](docs/DEFENSE_CHECKLIST.md), [hướng dẫn OCR](docs/OCR_REVIEW_GUIDE.md).

## 1. Chuẩn bị backend

Mở PowerShell tại repository:

```powershell
cd BE
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python .\scripts\configure_local.py
```

Lệnh cấu hình sẽ hỏi mật khẩu PostgreSQL hai lần ở chế độ ẩn, tự URL encode ký tự đặc
biệt, tự sinh `JWT_SECRET` và tạo `BE/.env`. Mật khẩu không xuất hiện trong câu lệnh hay
màn hình. Nếu `BE/.env` đã tồn tại, script chỉ ghi đè khi bạn gõ `GHI_DE`; có thể xem các
tùy chọn bằng `python .\scripts\configure_local.py --help`. Không commit file `.env` hoặc
gửi mật khẩu lên GitHub.

Tạo bảng bằng migration:

```powershell
alembic upgrade head
```

Chạy API:

```powershell
uvicorn app.main:app --reload --port 8000
```

Swagger được mở tại `http://localhost:8000/docs`.

## 2. Chạy worker nhắc thuốc

Mở PowerShell thứ hai:

```powershell
cd BE
.\.venv\Scripts\Activate.ps1
python -m app.worker
```

Worker tạo từng lần uống, lên lịch nhắc, dừng nhắc khi có phản hồi và báo OWNER khi hết thời gian xác nhận.

## 3. Chạy frontend

Mở PowerShell thứ ba:

```powershell
cd FE
npm install
Copy-Item .env.example .env.local
npm run dev
```

Mở `http://localhost:3000`.

## Luồng kiểm thử thủ công

> Nhánh này thêm mã phòng. Với database đã cài, dừng launcher và chạy migration một lần từ thư mục gốc: `cd BE`, rồi `./.venv/Scripts/python.exe -m alembic upgrade head`. Quay lại thư mục gốc rồi chạy launcher. Không cần tạo lại database hay sửa `.env`.

1. Đăng ký tài khoản. Hệ thống tự đăng nhập nhưng chưa tạo nhóm.
2. Đặt tên và tạo nhóm chăm sóc ở màn hình tiếp theo; tài khoản trở thành `OWNER`.
3. Tạo hồ sơ người được chăm sóc.
4. Người chăm sóc đăng ký tài khoản riêng và chưa tạo nhóm. Chủ nhóm có thể tạo lời mời cho email đã đăng ký rồi chia sẻ liên kết, hoặc tạo mã phòng trong mục **Người chăm sóc**.
5. Người chăm sóc đăng nhập để chấp nhận liên kết đúng email hoặc nhập mã phòng trên màn hình chưa có nhóm. Mã dùng trong 24 giờ, có thể kèm mật khẩu, tạo mã mới sẽ vô hiệu mã cũ. Tham gia không tự cấp quyền xem hồ sơ.
6. Chủ nhà phân công người chăm sóc cho hồ sơ và chọn từng quyền.
7. Chủ nhà tạo lịch thuốc.
8. Chạy worker một chu kỳ hoặc chờ tới giờ đã đặt.
9. Người chăm sóc mở trang `Hôm nay` và phản hồi lần uống.
10. Chủ nhà kiểm tra trạng thái và người đã phản hồi.

Kiểm tra email trong database chỉ xác nhận đã có tài khoản HealthGuard. Hiện chưa gửi email tự động và chưa xác minh quyền sở hữu hộp thư. Hồ sơ mới bắt buộc có tên và số điện thoại liên hệ khẩn cấp; hồ sơ cũ không tự thay đổi.

## Kiểm tra mã nguồn

Backend:

```powershell
cd BE
.\.venv\Scripts\Activate.ps1
pytest
```

Frontend:

```powershell
cd FE
npm test
npm run lint
npm run typecheck
npm run build
```

Xem hợp đồng và giới hạn module tại [`docs/MODULE_SCOPE.md`](docs/MODULE_SCOPE.md).
