# HealthGuard Backend

Backend FastAPI cho phần hồ sơ người cao tuổi, hai vai trò `OWNER`/`CAREGIVER`, lời mời,
phân công chăm sóc, lịch thuốc, xác nhận lần uống và worker nhắc Telegram.

Phần này không chứa dashboard thống kê, AI té ngã hay tư vấn y khoa. Trạng thái
`UNCONFIRMED` chỉ có nghĩa là hệ thống chưa nhận được xác nhận.

## Công nghệ

- Python 3.12 trở lên (đã kiểm thử với Python 3.14)
- FastAPI, SQLAlchemy 2 async và `asyncpg`
- PostgreSQL 18; Alembic quản lý schema
- JWT trong cookie `HttpOnly`; mỗi phiên được lưu trong database để logout có thể thu hồi
- Argon2id để băm mật khẩu
- Worker riêng tạo lần uống và đẩy thông báo qua Telegram Bot API

## Chuẩn bị PostgreSQL

Database và tài khoản kỹ thuật có thể tạo trong pgAdmin bằng tài khoản `postgres`:

```sql
CREATE ROLE eldercare_app WITH LOGIN PASSWORD 'THAY_MAT_KHAU_MANH';
CREATE DATABASE eldercare_dev OWNER postgres;
GRANT CONNECT ON DATABASE eldercare_dev TO eldercare_app;
```

Sau đó mở Query Tool của `eldercare_dev`:

```sql
GRANT USAGE, CREATE ON SCHEMA public TO eldercare_app;
```

`eldercare_app` là tài khoản kỹ thuật của backend. `OWNER` và `CAREGIVER` là vai trò
trong bảng `care_group_members`, không phải PostgreSQL role.

## Cài và chạy trên Windows

Từ thư mục `BE`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Mở `.env` và thay ít nhất:

```dotenv
DATABASE_URL=postgresql+asyncpg://eldercare_app:MAT_KHAU@localhost:5432/eldercare_dev
JWT_SECRET=CHUOI_NGAU_NHIEN_TOI_THIEU_32_KY_TU
```

Tạo secret ngẫu nhiên:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Nếu mật khẩu PostgreSQL chứa `@`, `:`, `/`, `#` hoặc ký tự đặc biệt khác, hãy URL encode
mật khẩu trước khi đưa vào `DATABASE_URL`.

Tạo bảng và chạy API:

```powershell
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --port 8000
```

Mở:

- API docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health>

Chạy worker ở terminal thứ hai, cùng `.env`:

```powershell
.\.venv\Scripts\Activate.ps1
python -m app.worker
```

Chạy đúng một chu kỳ để kiểm tra:

```powershell
python -m app.worker --once
```

## Cấu hình frontend và cookie

Mặc định frontend chạy tại `http://localhost:3000`. Backend đã bật CORS credentials cho
origin này. Frontend phải gửi `credentials: "include"` trong mọi request cần đăng nhập.

Sau khi đăng nhập, response `/api/v1/auth/me` trả `default_group_id`. Frontend gửi giá trị
đó qua header sau khi người dùng chọn nhóm:

```text
X-Care-Group-ID: <UUID>
```

Cookie local để `COOKIE_SECURE=false`. Khi triển khai HTTPS, đặt:

```dotenv
ENVIRONMENT=production
COOKIE_SECURE=true
JWT_SECRET=<secret-production-riêng>
```

## Telegram

Phần thuốc vẫn chạy khi chưa cấu hình Telegram; các lần gửi sẽ được lưu là chưa giao.
Để bật Telegram:

1. Tạo bot bằng BotFather.
2. Điền `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` và một
   `TELEGRAM_WEBHOOK_SECRET` ngẫu nhiên trong `.env`.
3. Public backend qua HTTPS và đăng ký webhook của Telegram tới:
   `/api/v1/integrations/telegram/webhook` với cùng secret token.
4. Người dùng đăng nhập rồi gọi `POST /api/v1/auth/telegram-link`.
5. Mở `deep_link` trả về và nhấn Start trong Telegram trong vòng 15 phút.

Backend không nhận `chat_id` trực tiếp từ form người dùng. Webhook đã xác thực sẽ liên kết
`chat_id` với tài khoản. Tin nhắc có nút mở:
`/hom-nay?occurrence=<UUID>` trên frontend.

## Endpoint chính

Tất cả nằm dưới `/api/v1`:

| Nhóm | Endpoint |
|---|---|
| Auth | `POST /auth/register-owner`, `/auth/register-caregiver`, `/auth/login`, `/auth/logout`; `GET /auth/me` |
| Thành viên | `GET /care-groups/current/members`, `POST /care-groups/current/invitations`, `DELETE /care-groups/current/members/{user_id}` |
| Lời mời | `GET /invitations/{token}` |
| Người cao tuổi | `GET/POST /elders`, `GET/PATCH /elders/{id}` |
| Phân công | `PUT/DELETE /elders/{elder_id}/caregivers/{user_id}` |
| Lịch thuốc | `GET/POST /elders/{elder_id}/medication-schedules`, `PATCH/DELETE /medication-schedules/{id}` |
| Lần uống | `GET /dose-occurrences?date=YYYY-MM-DD`, `POST /dose-occurrences/{id}/responses` |

Swagger tại `/docs` là nguồn chi tiết cho request/response hiện hành.

## Quy tắc phân quyền

- `OWNER` quản lý hồ sơ, lời mời, phân công và lịch thuốc trong đúng nhóm của mình.
- `CAREGIVER` chỉ thấy người cao tuổi được phân công.
- Ba quyền phân công gồm `can_view_medications`, `can_confirm_doses` và
  `can_view_diagnoses`.
- Backend kiểm tra quyền ở mọi truy vấn; frontend ẩn nút không được xem là biện pháp bảo mật.
- Token lời mời chỉ trả một lần, database chỉ lưu SHA-256 hash, có hạn dùng và dùng một lần.
- Phản hồi giống hệt gửi lại không tạo bản ghi trùng; phản hồi khác sau khi đã xác nhận trả `409`.

## Worker nhắc thuốc

Worker:

1. Tạo `dose_occurrences` trước tối đa 36 giờ theo timezone IANA của lịch.
2. Gửi nhắc theo `reminder_offsets_minutes`, mặc định `0, 15, 30, 45` phút.
3. Dừng nhắc khi nhận phản hồi.
4. Sau `escalation_after_minutes`, mặc định 60 phút, chuyển thành `UNCONFIRMED` và báo OWNER.
5. Dùng unique constraint, khóa hàng và outbox `notification_attempts` để tránh tạo/gửi trùng khi
   nhiều worker chạy đồng thời.

Timestamp được lưu dưới dạng UTC/timestamptz. Timezone mặc định của lịch là
`Asia/Ho_Chi_Minh`.

## Kiểm thử

```powershell
python -m pytest -q -p no:cacheprovider
python -m compileall app
```

Các test hiện có kiểm tra đăng ký/đăng nhập/logout, Argon2, cô lập dữ liệu giữa gia đình,
lời mời đúng email, phân quyền caregiver, tạo lịch, xác nhận idempotent và worker chuyển
lần quá hạn sang `UNCONFIRMED` mà không tạo cảnh báo trùng.

## Migration

```powershell
python -m alembic current
python -m alembic upgrade head
python -m alembic downgrade -1
```

Không tạo hoặc sửa bảng bằng chuột trong pgAdmin. Mọi thay đổi schema phải có migration
Alembic mới và được commit cùng mã nguồn.

