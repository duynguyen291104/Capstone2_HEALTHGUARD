# Bộ 9 video demo cho FallGuard Web

Thư mục này chứa 9 video URFD đã được chọn để kiểm tra đúng luồng upload của
dashboard. Cả 9 file MP4 được lưu trong Git để máy mới có thể lấy về bằng
`git clone` hoặc `git pull --ff-only` và test ngay.

## Web dùng để làm gì?

Trang web là giao diện **phân tích video có sẵn và hậu kiểm sự kiện**, khác với
lệnh webcam realtime:

| Chế độ | Nguồn vào | Mục đích |
| --- | --- | --- |
| `fallguard infer camera` | Webcam/USB camera | Theo dõi trực tiếp trong cửa sổ desktop |
| Dashboard web | File MP4/MOV/AVI/MKV/WEBM | Upload cục bộ, phân tích lại, xem video chú thích, JSON, lịch sử và phản hồi |

"Upload lên web" ở đây không có nghĩa là gửi video lên Internet. Khi chạy tại
`127.0.0.1`, trình duyệt gửi file tới FastAPI trên chính máy tính này. File
upload tạm được xóa sau khi xử lý; video chú thích, JSON và lịch sử SQLite được
lưu dưới `runtime/`.

- `Camera ID`: nhãn nguồn video, ví dụ `CAM-DEMO-01`; không phải số webcam.
- `Person ID`: mã người để tra cứu sau này, có thể để trống.
- `Video mẫu URFD`: crop nửa RGB bên phải vì video gốc ghép depth + RGB.
- `Kết quả phân tích`: trạng thái cuối, mức nguy cơ, số frame, pose và timeline.
- `Lịch sử sự kiện`: chỉ lưu những sự kiện đã được xác nhận; người kiểm duyệt có
  thể đánh dấu đúng/sai.

## Mở web demo

Repo có sẵn checkpoint `artifacts/urfd_preview_gru/best.pt` và model pose
full/lite trong `data/models/`, nên máy mới không cần huấn luyện lại.
Nếu đã clone bản cũ, chạy `git pull --ff-only` trong thư mục dự án.
Cài môi trường lần đầu bằng `powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1`.
Video mẫu MP4 có sẵn trong thư mục này; cũng có thể upload video của bạn để phân tích.
Thông tin Telegram trong `.env` phải cấu hình riêng trên mỗi máy.

Từ thư mục gốc dự án:

```powershell
powershell -ExecutionPolicy Bypass -File .\demo_videos\START_WEB_DEMO.ps1
```

Script mở <http://127.0.0.1:8000> với checkpoint thật và
`configs/urfd_demo.yaml`. Dừng server bằng `Ctrl+C` trong PowerShell.

## Cách thử

1. Chọn `01_FALL_CONFIRMED_URFD_fall-19.mp4`.
2. Web tự bật ô **Video mẫu URFD**; nếu chưa bật thì đánh dấu thủ công.
3. Giữ `Camera ID` hoặc nhập tên khác, rồi nhấn **Phân tích video**.
4. Chờ khoảng vài giây.
5. Xem trạng thái bên phải, mở video chú thích và tải JSON.
6. Thử tiếp các mẫu `ADL_NORMAL` để so sánh.

Hai file đầu là đường demo chính, đã được xác minh ra `CONFIRMED_FALL`. Các file
`EDGE_VERIFYING` là ca biên cố ý giữ lại để trình bày giới hạn của mô hình:
chúng tạo tín hiệu nghi ngờ nhưng chưa đủ bằng chứng để xác nhận.

Kết quả kỳ vọng chi tiết nằm trong [catalog.csv](catalog.csv). Kết quả được đo
với `artifacts/urfd_preview_gru/best.pt`, `configs/urfd_demo.yaml` và tùy chọn
URFD crop. Thay checkpoint hoặc cấu hình có thể làm kết quả thay đổi.

Ảnh annotated đã trích sẵn để thử lệnh gửi Telegram nằm trong
[`telegram_samples/`](telegram_samples/README.md).

## Nguồn và giấy phép

Video thuộc UR Fall Detection Dataset (URFD), dùng cho học thuật phi thương mại
theo CC BY-NC-SA 4.0:
<https://fenix.ur.edu.pl/~mkepski/ds/uf.html>.

Không dùng bộ video này như bằng chứng về độ an toàn y tế hoặc hiệu năng ngoài
đời thực.
