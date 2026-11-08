# Demo mục Phát hiện té ngã

Thay đổi chỉ ở FE. HealthGuard vẫn dùng launcher cũ; trang `/te-nga` nằm trong khu vực đăng nhập. Bản này dành cho demo trên cùng máy, chưa phải tích hợp dữ liệu bệnh nhân.

1. Tại thư mục gốc, chạy `./run-healthguard.cmd --local-only` nếu không cần Telegram nhắc thuốc. Nếu cần kênh nhắc thuốc đã cấu hình, chạy launcher không có cờ này.
2. Mở PowerShell khác tại thư mục gốc. Sau khi đã cài môi trường AI theo README của nhóm, chạy:

   ```powershell
   cd .\AI
   .\.venv\Scripts\fallguard.exe serve --config .\configs\urfd_demo.yaml --checkpoint .\artifacts\urfd_preview_gru\best.pt --port 8001
   ```

3. Mở HealthGuard tại `http://localhost:3000`, đăng nhập, chọn **Phát hiện té ngã → Mở bảng FallGuard trong trang**. Nếu khung không tải, kiểm tra terminal AI và thử nút **Mở tab riêng**.
4. Dùng clip `AI/demo_videos/01_FALL_CONFIRMED_URFD_fall-19.mp4`, bật **Video mẫu URFD** rồi phân tích.

## Phân biệt hai chế độ

- **Kiểm tra webcam** chỉ xem hình cục bộ, không truyền hình đến AI. Tắt khung xem thử trước khi chạy webcam từ terminal.
- **Nhận diện trực tiếp** vẫn do lệnh `fallguard infer camera` của nhóm thực hiện. Web hiện chưa điều khiển tiến trình hoặc nhận luồng khung hình chú thích. Telegram AI vẫn dùng cấu hình riêng, không dùng cấu hình nhắc thuốc.
- **Bảng video/lịch sử** nhúng dashboard đang chạy ở loopback 8001, không gọi vòng qua BE hoặc sửa CORS. Đăng nhập HealthGuard không bảo vệ API FallGuard; không mở AI ra mạng và không dùng video/hồ sơ thật.

## Phần cần nhóm AI nối sau

Thống nhất nguồn stream có chú thích và trạng thái kết nối, API nhận sự kiện/ảnh, liên kết camera với mã hồ sơ, phân quyền theo nhóm phía server và trạng thái gửi cảnh báo. Khi có API, thay khung nhúng bằng giao diện dùng dữ liệu đã xác thực. Không đưa token Telegram vào biến công khai của Next.js.
