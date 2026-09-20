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
- Telegram là kênh thông báo tùy chọn; thao tác xác nhận được thực hiện trong web sau khi đăng nhập.

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
- Node.js 20 trở lên và npm.

Docker không bắt buộc cho môi trường phát triển hiện tại.

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

1. Đăng ký tài khoản. Hệ thống tự đăng nhập nhưng chưa tạo nhóm.
2. Đặt tên và tạo nhóm chăm sóc ở màn hình tiếp theo; tài khoản trở thành `OWNER`.
3. Tạo hồ sơ người được chăm sóc.
4. Tạo lời mời và sao chép đường dẫn mời.
5. Mở đường dẫn ở cửa sổ ẩn danh. Người chăm sóc có thể tạo tài khoản mới hoặc đăng nhập tài khoản chưa thuộc nhóm để chấp nhận lời mời.
6. Chủ nhà phân công người chăm sóc cho hồ sơ và chọn từng quyền.
7. Chủ nhà tạo lịch thuốc.
8. Chạy worker một chu kỳ hoặc chờ tới giờ đã đặt.
9. Người chăm sóc mở trang `Hôm nay` và phản hồi lần uống.
10. Chủ nhà kiểm tra trạng thái và người đã phản hồi.

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
npm run lint
npm run typecheck
npm run build
```

Xem hợp đồng và giới hạn module tại [`docs/MODULE_SCOPE.md`](docs/MODULE_SCOPE.md).
