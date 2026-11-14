# Chạy FallGuard trong HealthGuard

## Cập nhật và chạy

1. Dừng HealthGuard và dashboard FallGuard cũ (nếu đang dùng cổng 8001). Từ thư mục gốc, cập nhật database một lần:

   ```powershell
   cd .\BE
   .\.venv\Scripts\python.exe -m alembic upgrade head
   cd ..
   ```

2. Nếu máy chưa có `AI\.venv`, cài theo [README AI](../AI/README.md). Không cần huấn luyện lại; dùng checkpoint và pose model đã có trong repo.
3. Từ thư mục gốc, chạy `./run-healthguard.cmd --with-ai`. Nếu chỉ muốn thử không gửi Telegram, dùng `./run-healthguard.cmd --with-ai --local-only`. Mở http://localhost:3000, đăng nhập, vào **Phát hiện té ngã**. Không cần mở thêm terminal AI.

## Camera và video

- **Camera nhận diện trực tiếp:** Chọn hồ sơ, chọn số webcam (thường là 0), nhập mã bật camera in trên PowerShell rồi bấm **Bật camera AI**. Đây là webcam gắn vào máy chạy dịch vụ, không phải camera trên điện thoại đang mở web. Giữ trang mở; khi mất kết nối, phiên tự hết hạn. Không chạy song song lệnh camera cũ.
- **Video:** Chọn clip tối đa 50 MB, 120 giây rồi phân tích. Với `AI/demo_videos/01_FALL_CONFIRMED_URFD_fall-19.mp4`, bật **Video mẫu URFD** để tách nửa RGB. Chế độ URFD không gửi Telegram. Video kết quả nằm trong thư mục tạm của dịch vụ, có thời hạn một giờ và không phát qua URL công khai.
- Cả hai luồng dùng pipeline FallGuard của nhóm, không huấn luyện lại và không sửa lệnh CLI gốc. Lịch sử trên web ghi sự kiện AI xác nhận và tình trạng gửi theo hồ sơ; không nhập lịch sử SQLite từ dashboard AI cũ.

## Một bot cho hai loại thông báo

Vào **Kết nối thông báo → Tạo liên kết Telegram → Mở Telegram → Start**, rồi kiểm tra trạng thái trên web. Nếu đã liên kết **cùng bot HealthGuard hiện tại** thì dùng lại được. Bot nhận biết cuộc trò chuyện bằng `chat_id`, không phải số điện thoại hồ sơ.

- Tin nhắc thuốc và tin quá hạn chỉ gửi chủ nhóm đã liên kết bot, kể cả khi lịch giao riêng cho một người chăm sóc. Người chăm sóc vẫn xem lịch và phản hồi trên web theo quyền.
- Khi người chăm sóc xác nhận **Đã cho uống** trên web, worker gửi kết quả vào cùng cuộc trò chuyện Telegram của chủ nhóm. Tin có tên người được chăm sóc, thuốc, người xác nhận và thời điểm cho uống. Chủ nhóm tự xác nhận không tạo tin kết quả gửi lại cho chính mình.
- Cảnh báo té ngã cũng chỉ gửi chủ nhóm đã liên kết bot, nếu bật ô gửi thật trước khi mở camera hoặc phân tích video thường. Cảnh báo là tin nhắn, không đính kèm ảnh/video. Bot chấp nhận gửi không có nghĩa người nhận đã đọc.
- Chỉ cấu hình Telegram của HealthGuard trong `BE/.env`. Không chép token sang FE hoặc khai báo Chat ID cố định cho luồng mới. Lệnh FallGuard cũ vẫn có cấu hình riêng nếu chạy độc lập; không chạy song song để tránh cảnh báo trùng.
- Sự kiện trùng ID không gửi lại. Hiện không có hàng đợi gửi lại tự động nếu bot từ chối hoặc mất kết nối; xem trạng thái ở lịch sử. Đây là công cụ hỗ trợ, không dùng làm kênh cấp cứu duy nhất.

## Triển khai và kiểm thử

Dịch vụ AI chỉ lắng nghe loopback cổng 8001 và yêu cầu khóa nội bộ do launcher sinh. FE đi qua API có đăng nhập và quyền theo hồ sơ. Mã bật camera là bí mật của phiên chạy, không đưa lên Git hoặc chia sẻ ngoài nhóm được phép. Chỉ dùng dữ liệu mẫu cho demo. Trước khi mở ra mạng, cần HTTPS và kiểm thử quyền, đồng thời rà soát vận hành camera và chính sách dữ liệu.
