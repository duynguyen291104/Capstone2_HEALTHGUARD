# Cảnh báo té ngã qua Telegram

FallGuard có thể gửi cảnh báo tiếng Việt đến Telegram khi state machine xác nhận
sự kiện `CONFIRMED_FALL`. Với webcam và video, cảnh báo kèm ảnh JPEG đã vẽ
skeleton/trạng thái tại thời điểm xác nhận. Endpoint chỉ nhận skeleton không có
pixel gốc nên chỉ gửi tin nhắn văn bản.

Telegram là kênh cảnh báo phụ cho bản demo capstone, không phải cơ chế gọi cấp
cứu duy nhất. Bot API, `sendMessage`, `sendPhoto` và `getUpdates` được mô tả
trong [Telegram Bot API chính thức](https://core.telegram.org/bots/api).

## 1. Thu hồi token đã lộ trước tiên

Nếu token từng được dán vào chat, ảnh chụp màn hình, Git hoặc source code, hãy
xem token đó là **đã bị lộ**:

1. Mở cuộc trò chuyện với [@BotFather](https://t.me/BotFather).
2. Gửi `/revoke`, chọn bot cần thu hồi và tạo token thay thế theo hướng dẫn.
3. Không dán token mới vào chat, issue, báo cáo, source code hay commit Git.
4. Chỉ đặt token mới trong biến môi trường trên máy chạy FallGuard.

Tên bot dạng `@TenBot` hoặc liên kết `t.me/TenBot` là địa chỉ công khai, **không
phải Chat ID** và không thay thế được `FALLGUARD_TELEGRAM_CHAT_ID`. Telegram
hướng dẫn cách tạo/quản lý bot qua BotFather trong
[Bot tutorial chính thức](https://core.telegram.org/bots/tutorial).

## 2. Mở bot và tạo một update

Bot không thể tự bắt đầu cuộc trò chuyện với tài khoản cá nhân. Trên Telegram:

1. Mở bot bằng tên hoặc liên kết công khai của bot.
2. Nhấn **Start** hoặc gửi `/start`.
3. Nếu muốn gửi vào nhóm, thêm bot vào nhóm rồi gửi một tin nhắn trong nhóm.

Bước này tạo update để lệnh discovery có thể tìm ra Chat ID.

## 3. Cấu hình trong PowerShell

Cách dễ và an toàn nhất là mở PowerShell tại **thư mục gốc dự án** rồi chạy
script thiết lập:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\configure_telegram.ps1
```

Script yêu cầu xác nhận token cũ đã được thu hồi, đọc token mới ở chế độ ẩn,
hướng dẫn tạo update, chạy discovery, hỏi Chat ID, lưu cấu hình vào `.env` và
gửi ảnh mẫu. Token không nằm trong tham số dòng lệnh hoặc lịch sử PowerShell.
File `.env` đã được `.gitignore` loại khỏi Git nhưng vẫn chứa secret dạng văn
bản trên máy, vì vậy không chia sẻ file này.

Nếu muốn cấu hình thủ công, kích hoạt môi trường rồi đặt **token mới**:

```powershell
.\.venv\Scripts\Activate.ps1
$env:FALLGUARD_TELEGRAM_BOT_TOKEN = "<TOKEN_MOI_TU_BOTFATHER>"
fallguard telegram discover
```

Không nhập token thật vào tài liệu hoặc file `.env.example`. Lệnh discovery sẽ
liệt kê Chat ID của các tài khoản/nhóm vừa gửi tin nhắn cho bot. Chọn đúng ID rồi
đặt một người nhận:

```powershell
$env:FALLGUARD_TELEGRAM_CHAT_ID = "<CHAT_ID>"
$env:FALLGUARD_TELEGRAM_ENABLED = "true"
```

Hoặc nhiều người nhận, phân tách bằng dấu phẩy, dấu chấm phẩy hoặc khoảng trắng:

```powershell
$env:FALLGUARD_TELEGRAM_CHAT_IDS = "<CHAT_ID_1>,<CHAT_ID_2>"
$env:FALLGUARD_TELEGRAM_ENABLED = "true"
```

Nếu cả hai biến Chat ID cùng tồn tại, `FALLGUARD_TELEGRAM_CHAT_IDS` được ưu tiên.
Có thể chỉnh thời gian chờ mạng, mặc định là 8 giây:

```powershell
$env:FALLGUARD_TELEGRAM_TIMEOUT_SECONDS = "8"
```

FallGuard tự đọc file `.env` ở thư mục đang chạy và không ghi đè biến `$env:`
đã được đặt trong PowerShell. Vì vậy hãy luôn chạy lệnh từ thư mục gốc dự án.
`.env.example` chỉ chứa tên biến mẫu và không được dùng để lưu secret.

## 4. Gửi tin nhắn thử

Sau khi đã có token mới và Chat ID:

```powershell
fallguard telegram test
```

Muốn kiểm tra cả luồng gửi ảnh, truyền một file JPEG có sẵn:

```powershell
fallguard telegram test `
  --snapshot .\demo_videos\telegram_samples\01_FALL_CONFIRMED_snapshot.jpg
```

Ảnh mẫu này được chính pipeline trích từ video té số 01 và đã có skeleton, nhãn
`CONFIRMED_FALL` cùng xác suất. Nếu lệnh thành công, Telegram sẽ nhận cảnh báo
thử. Đây chỉ kiểm tra kết nối bot, không tạo sự kiện té ngã trong cơ sở dữ liệu.

## 5. Demo phát hiện trực tiếp

### Webcam

Giữ các biến môi trường ở cùng cửa sổ PowerShell rồi chạy:

```powershell
fallguard infer camera `
  --source 0 `
  --checkpoint artifacts/urfd_preview_gru/best.pt
```

Khi state machine chuyển sang `CONFIRMED_FALL`, FallGuard lưu ảnh bằng chứng đã
annotate và gửi một lần đến từng Chat ID. Việc gửi chạy tách khỏi vòng lặp camera
để lỗi mạng không làm dừng phát hiện. Nhấn `Q` hoặc `Esc` để kết thúc.

### Dashboard/API và video

Server đọc cấu hình Telegram khi khởi động. Nếu dashboard đã chạy trước lúc tạo
`.env`, dừng server cũ bằng `Ctrl+C` rồi khởi động lại:

```powershell
fallguard serve `
  --checkpoint artifacts/urfd_preview_gru/best.pt `
  --open
```

Tải video ở dashboard hoặc gọi `POST /v1/predict/video`. Khi video có sự kiện
`CONFIRMED_FALL`, ảnh được trích từ video kết quả đã annotate tại mốc xác nhận và
được gửi qua Telegram. `POST /v1/predict/skeleton` chỉ chứa tọa độ khớp nên cảnh
báo của endpoint này là text-only.

Thông báo được chống gửi trùng theo `event_id`: cùng một sự kiện có thể xuất
hiện trong timeline và danh sách kết quả cuối nhưng chỉ được xếp hàng gửi một
lần. Trạng thái nghi ngờ như `POSSIBLE_FALL` không gửi cảnh báo.

## Quyền riêng tư

Ảnh gửi qua bot sẽ rời máy tính và được tải lên hạ tầng Telegram. Chỉ bật tính
năng này khi người xuất hiện trong video đã đồng ý và chính sách của đồ án cho
phép. Không quay khu vực riêng tư; giới hạn người có Chat ID được cấu hình; thu
hồi token khi nghi ngờ bị lộ. Snapshot cục bộ cũng cần được bảo vệ và xóa theo
chính sách lưu trữ của nhóm.

## Xử lý lỗi thường gặp

### `discover` không thấy Chat ID

- Kiểm tra đã nhấn **Start** hoặc gửi `/start` cho đúng bot sau khi đổi token.
- Gửi thêm một tin nhắn mới rồi chạy lại `fallguard telegram discover`.
- Với nhóm, kiểm tra bot đã được thêm vào nhóm và nhóm vừa có tin nhắn mới.
- Tên bot/username không phải Chat ID; không đặt `t.me/...` vào biến Chat ID.

### `401 Unauthorized`

Token sai, hết hiệu lực hoặc đã bị thu hồi. Lấy token mới từ BotFather và đặt lại
biến môi trường; không in token ra terminal để chụp màn hình.

### `400 Bad Request: chat not found`

Chat ID sai hoặc người dùng chưa bắt đầu cuộc trò chuyện với bot. Gửi `/start`,
discovery lại và sao chép đúng ID; Chat ID nhóm có thể là số âm.

### `409 Conflict` hoặc thông báo liên quan webhook/getUpdates

Discovery dùng
[`getUpdates`](https://core.telegram.org/bots/api#getupdates), phương thức này
không hoạt động khi bot đang có webhook. Dừng các chương trình khác đang poll
cùng bot. Nếu bot từng được cấu hình webhook, xóa webhook bằng request POST sau;
lệnh chỉ tham chiếu biến môi trường nên không ghi token thật vào lịch sử lệnh:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "https://api.telegram.org/bot$($env:FALLGUARD_TELEGRAM_BOT_TOKEN)/deleteWebhook" `
  -Body @{ drop_pending_updates = "false" }
```

Sau đó gửi `/start` hoặc một tin nhắn mới và chạy discovery lại. Tham khảo
[`deleteWebhook`](https://core.telegram.org/bots/api#deletewebhook) trong tài
liệu chính thức.

### Không có ảnh nhưng vẫn có tin nhắn

Đây là hành vi dự phòng có chủ đích: nếu nguồn chỉ có skeleton, file snapshot
không đọc được hoặc không trích được frame, hệ thống vẫn gửi text để không làm
mất cảnh báo. Webcam/video hợp lệ mới có pixel để tạo ảnh annotated.

### Không nhận được cảnh báo khi chạy FallGuard

- Chạy `fallguard telegram test` trước để tách lỗi cấu hình khỏi lỗi model.
- Kiểm tra token và Chat ID được đặt trong đúng cửa sổ PowerShell đang chạy.
- Kiểm tra `FALLGUARD_TELEGRAM_ENABLED` không phải `false`.
- Cảnh báo thật chỉ gửi sau `CONFIRMED_FALL`; video không vượt ngưỡng xác nhận sẽ
  không gửi.
- Xem log terminal để biết lỗi mạng/Telegram; lỗi gửi không làm dừng inference.
