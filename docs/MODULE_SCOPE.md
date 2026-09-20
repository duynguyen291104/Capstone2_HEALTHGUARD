# Medication Care Module: phạm vi và hợp đồng tích hợp

Tài liệu này chốt phạm vi phần việc quản lý chăm sóc và nhắc thuốc. Đây là hợp đồng chung giữa `FE` và `BE`; phần AI té ngã chỉ tích hợp qua định danh người cao tuổi.

## 1. Phạm vi hiện tại

### Có trong lần triển khai này

- Đăng ký, đăng nhập, đăng xuất và xem phiên đăng nhập hiện tại.
- Hai vai trò trong ứng dụng: `OWNER` và `CAREGIVER`.
- Chủ nhà tạo nhóm chăm sóc, mời người chăm sóc và thu hồi quyền.
- Chủ nhà tạo, sửa và xem hồ sơ người cao tuổi.
- Phân công người chăm sóc theo từng người cao tuổi.
- Chủ nhà tạo và thay đổi lịch thuốc.
- Hiển thị các lần uống thuốc cần thực hiện.
- Người chăm sóc xác nhận `Đã cho uống` hoặc `Chưa thể cho uống`.
- Lưu người thao tác, thời gian thao tác và lý do để truy vết.
- Nền tảng dữ liệu cho worker nhắc lại và Telegram.

### Chưa làm trong lần này

- Dashboard biểu đồ, thống kê nâng cao và báo cáo quản trị.
- Chẩn đoán y khoa, tư vấn đổi liều hoặc hướng dẫn uống bù.
- Kết luận người chăm sóc nói dối chỉ từ việc bấm hoặc không bấm nút.
- Camera, nhận diện té ngã và xử lý video.

Giao diện tối thiểu vẫn cần các trang vận hành: đăng ký/đăng nhập, thành viên, hồ sơ người cao tuổi, lịch thuốc và danh sách lần uống cần xử lý. Đây không phải dashboard thống kê.

## 2. Ranh giới dữ liệu giữa các module

- `users`, `care_groups`, `care_group_members` và `elder_profiles` là dữ liệu dùng chung.
- Module nhắc thuốc sở hữu `caregiver_assignments`, `invitations`, `medication_schedules`, `dose_occurrences`, `dose_responses`, `notification_attempts` và `audit_logs`.
- Module AI té ngã tham chiếu `elder_profiles.id`; không sao chép một hồ sơ người cao tuổi khác.
- `OWNER` và `CAREGIVER` là vai trò của ứng dụng trong `care_group_members`, không phải PostgreSQL role.
- Trong phạm vi hiện tại, mỗi tài khoản chỉ thuộc một nhóm chăm sóc. Tài khoản tự tạo nhóm trở thành `OWNER`; tài khoản nhận lời mời trở thành `CAREGIVER`. Mọi truy vấn vẫn phải kiểm tra `care_group_id` và membership hiện tại.

## 3. Hợp đồng API giữa FE và BE

Quy ước chung:

- Base URL: `/api/v1`.
- Request và response dùng JSON, trừ endpoint tải tệp nếu bổ sung sau.
- ID dùng UUID dạng chuỗi; thời gian dùng ISO 8601 có múi giờ.
- Auth dùng cookie `HttpOnly`; FE luôn gửi request với `credentials: "include"`.
- Thành công trả đúng mã `200`, `201` hoặc `204`; lỗi dùng một cấu trúc thống nhất:

```json
{
  "error": {
    "code": "INVITATION_EXPIRED",
    "message": "Lời mời đã hết hạn",
    "fields": null
  }
}
```

### Auth

| Method | Endpoint | Quyền | Mục đích |
|---|---|---|---|
| `POST` | `/auth/register` | Public | Tạo tài khoản và phiên đăng nhập; chưa gán vai trò hoặc nhóm |
| `POST` | `/auth/register-caregiver` | Public + invitation token | Tạo tài khoản người chăm sóc từ lời mời còn hiệu lực |
| `POST` | `/auth/login` | Public | Xác thực và đặt cookie phiên |
| `POST` | `/auth/logout` | Logged in | Xóa cookie phiên |
| `GET` | `/auth/me` | Logged in | Trả người dùng, nhóm hiện tại và vai trò |

Payload tối thiểu khi đăng ký tài khoản:

```json
{
  "full_name": "Nguyen Van An",
  "email": "an@example.com",
  "password": "a-strong-password"
}
```

### Nhóm, lời mời và phân quyền

| Method | Endpoint | Quyền | Mục đích |
|---|---|---|---|
| `POST` | `/care-groups` | Logged in, chưa thuộc nhóm | Tạo nhóm và gán tài khoản hiện tại làm `OWNER` |
| `GET` | `/care-groups/current/members` | OWNER | Danh sách thành viên của nhóm hiện tại |
| `POST` | `/care-groups/current/invitations` | OWNER | Tạo lời mời có hạn dùng và gắn đúng email |
| `GET` | `/invitations/{token}` | Public | Kiểm tra lời mời để hiện trang đăng ký |
| `DELETE` | `/care-groups/current/members/{user_id}` | OWNER | Thu hồi quyền người chăm sóc |
| `PUT` | `/elders/{elder_id}/caregivers/{user_id}` | OWNER | Phân công và cấp quyền theo người cao tuổi |
| `DELETE` | `/elders/{elder_id}/caregivers/{user_id}` | OWNER | Hủy phân công |

Lời mời phải dùng token dùng một lần, có thời hạn, có thể thu hồi và không lưu token gốc trong database.

### Hồ sơ người cao tuổi

| Method | Endpoint | Quyền | Mục đích |
|---|---|---|---|
| `GET` | `/elders` | OWNER / CAREGIVER được phân công | Danh sách hồ sơ người dùng được phép xem |
| `POST` | `/elders` | OWNER | Tạo hồ sơ |
| `GET` | `/elders/{elder_id}` | OWNER / CAREGIVER được phân công | Xem hồ sơ |
| `PATCH` | `/elders/{elder_id}` | OWNER | Chỉnh sửa hồ sơ |

Các trường ban đầu: `full_name`, `date_of_birth`, `height_cm`, `weight_kg`, `diagnosed_conditions`, `current_medications_note`, `mobility_level`, `sleep_habits`, `emergency_contact_name`, `emergency_contact_phone`.

### Lịch thuốc và xác nhận

| Method | Endpoint | Quyền | Mục đích |
|---|---|---|---|
| `GET` | `/elders/{elder_id}/medication-schedules` | Thành viên được phép xem | Xem lịch thuốc |
| `POST` | `/elders/{elder_id}/medication-schedules` | OWNER | Tạo lịch thuốc theo đơn |
| `PATCH` | `/medication-schedules/{schedule_id}` | OWNER | Thay đổi lịch từ thời điểm hiệu lực mới |
| `DELETE` | `/medication-schedules/{schedule_id}` | OWNER | Ngừng lịch, không xóa lịch sử cũ |
| `GET` | `/dose-occurrences?date=YYYY-MM-DD` | Logged in | Danh sách lần uống mà người dùng được phép xem/xử lý |
| `POST` | `/dose-occurrences/{occurrence_id}/responses` | CAREGIVER được phân công / OWNER | Xác nhận kết quả một lần uống |

Payload phản hồi:

```json
{
  "status": "ADMINISTERED",
  "note": null,
  "administered_at": "2026-09-20T08:04:00+07:00"
}
```

Hoặc:

```json
{
  "status": "CANNOT_ADMINISTER",
  "reason_code": "ELDER_REFUSED",
  "note": "Nguoi duoc cham soc tu choi"
}
```

Trạng thái lần uống: `SCHEDULED`, `DUE`, `ADMINISTERED`, `CANNOT_ADMINISTER`, `UNCONFIRMED`, `CANCELLED`. Hết thời gian mà không có phản hồi phải là `UNCONFIRMED`, không tự ghi là đã bỏ thuốc. Việc gửi lại cùng một phản hồi không được tạo nhiều bản ghi hoặc làm sai trạng thái.

## 4. File ở root nên có

| File | Mục đích |
|---|---|
| `README.md` | Mô tả module, sơ đồ thư mục, yêu cầu môi trường và cách chạy FE/BE |
| `.gitignore` | Bỏ qua `.env`, `node_modules`, `.next`, Python virtualenv/cache, log, coverage và dữ liệu PostgreSQL cục bộ |
| `.env.example` | Tên biến môi trường mẫu, không chứa mật khẩu hoặc token thật |
| `compose.yaml` | Tùy chọn cho cả nhóm chạy PostgreSQL thống nhất; chưa bắt buộc vì máy hiện tại đã cài PostgreSQL 18 |
| `docs/MODULE_SCOPE.md` | Hợp đồng phạm vi và API này |

Với PostgreSQL cài trực tiếp, backend có thể dùng biến mẫu:

```text
DATABASE_URL=postgresql+asyncpg://eldercare_app:<PASSWORD>@localhost:5432/eldercare_dev
FRONTEND_ORIGIN=http://localhost:3000
```

Không commit `.env`. Nếu thêm `compose.yaml`, đổi cổng host khi `5432` đang được PostgreSQL cục bộ sử dụng hoặc tắt một trong hai dịch vụ để tránh xung đột.

## 5. Acceptance tests quan trọng

### Auth và phân quyền

1. Đăng ký tạo `user` và phiên nguyên tử. Tạo nhóm là bước riêng, tạo `care_group` và membership `OWNER` nguyên tử; lỗi ở một bước phải rollback toàn bộ.
2. Email sau khi chuẩn hóa là duy nhất; password chỉ lưu dưới dạng Argon2 hash.
3. Đăng nhập sai email và sai mật khẩu trả cùng một thông báo chung.
4. Đăng xuất làm phiên không còn dùng được; endpoint bảo vệ trả `401` khi chưa đăng nhập.
5. CAREGIVER không thể tự nâng quyền, tạo OWNER, sửa lịch thuốc hoặc xem người cao tuổi chưa được phân công.
6. OWNER nhóm A không thể đọc hoặc sửa bất kỳ dữ liệu nào của nhóm B, kể cả khi đoán được UUID.

### Lời mời và hồ sơ

7. Token lời mời hết hạn, đã dùng, bị thu hồi hoặc sai email đều bị từ chối.
8. Một lời mời chỉ chấp nhận được một lần; hai request đồng thời chỉ một request thành công.
9. Thu hồi thành viên có hiệu lực ngay với request tiếp theo.
10. OWNER tạo được nhiều hồ sơ; CAREGIVER chỉ thấy hồ sơ được phân công và đúng các quyền đã cấp.

### Thuốc và nhắc việc

11. Lịch thuốc tạo đúng các lần uống theo timezone của nhóm/lịch; mặc định là `Asia/Ho_Chi_Minh`. Việc đổi lịch không làm thay đổi lịch sử cũ.
12. Worker chạy lại hoặc chạy song song không tạo hai `dose_occurrences` hay gửi trùng một lần nhắc.
13. Xác nhận thành công dừng các lần nhắc tiếp theo; chỉ người được phân công mới xác nhận được.
14. Không phản hồi sau lần nhắc cuối chuyển thành `UNCONFIRMED`; hệ thống không kết luận tự động là đã bỏ liều.
15. Mọi thay đổi nhạy cảm có audit gồm người thực hiện, thời gian, đối tượng và giá trị thay đổi cần thiết.

## 6. Rủi ro tích hợp cần xử lý sớm

- **Rò rỉ dữ liệu giữa gia đình:** kiểm tra membership trong backend ở mọi truy vấn; không dựa vào việc FE ẩn nút.
- **Cookie khi FE và BE khác cổng:** cấu hình CORS đúng origin, cho phép credentials và không dùng wildcard origin.
- **Xung đột schema:** chốt một migration owner cho các bảng dùng chung; module AI chỉ tham chiếu khóa ngoại đã thống nhất.
- **Nhắc trùng hoặc mất nhắc:** tạo unique constraint cho mỗi lịch/thời điểm, transaction/locking trong worker và lưu từng notification attempt.
- **Múi giờ:** lưu timestamp theo UTC trong PostgreSQL, lưu timezone IANA trên nhóm hoặc từng lịch và dùng `Asia/Ho_Chi_Minh` làm mặc định.
- **Đổi hoặc ngừng đơn thuốc:** dùng hiệu lực từ thời điểm mới; không sửa/xóa các lần uống trong lịch sử.
- **Danh tính Telegram:** ánh xạ tài khoản Telegram qua quy trình liên kết có xác thực, không tin `chat_id` người dùng gửi trực tiếp.
- **Diễn giải sai dữ liệu:** `ADMINISTERED` là xác nhận của người thao tác; `UNCONFIRMED` chỉ là chưa có xác nhận.

## 7. Điều kiện ghép nhánh

- FE và BE dùng đúng endpoint, enum, mã lỗi và kiểu thời gian trong tài liệu này.
- Migration chạy được trên database rỗng và nâng cấp được database đang có.
- Không có secret thật trong Git.
- Các test tenant isolation và phân quyền bắt buộc phải qua trước khi merge.
- `README.md` có lệnh chạy cụ thể sau khi cấu trúc FE/BE được tạo.
