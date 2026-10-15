# Nhập đơn thuốc bằng ảnh — bản cải thiện

## Cách chạy và dùng

Trên máy hiện tại đã cài OpenCV và đã kiểm tra Tesseract tiếng Việt/Anh. Không cần Google Cloud, API key, thẻ thanh toán, Docker hoặc GPU.

1. Nếu ứng dụng đang chạy, nhấn **Ctrl+C** tại cửa sổ chạy HealthGuard. Không mở thêm phiên khi phiên cũ vẫn chiếm cổng.
2. Tại thư mục dự án, chạy `./run-healthguard.cmd` trong PowerShell.
3. Mở `http://localhost:3000`, đăng nhập bằng chủ nhóm, vào **Thuốc & lịch uống**, chọn người được chăm sóc.
4. Bấm **Tải ảnh đơn thuốc**, chọn JPG/PNG không quá 5 MB, xác nhận đúng người và quyền dùng ảnh; bấm **Đọc và điền form**.
5. Đối chiếu ảnh bên cạnh, sửa ô vàng. Có thể thêm/xóa lần dùng, thêm thuốc còn thiếu, bỏ chọn thuốc không cần nhập. Chọn giờ cụ thể nếu đơn chỉ ghi sáng/tối; không có giờ mặc định ngầm.
6. Kiểm tra ngày bắt đầu, các ngày dùng trong tuần, thời gian điều trị/ngày kết thúc và người phụ trách. Xác nhận đã đối chiếu, rồi bấm **Tạo … lịch đã kiểm tra**.

Nếu kết quả đọc kém, bấm **Bỏ bản OCR và nhập tay**: giữ ảnh và chữ OCR để tham khảo, không phải tải lại hoặc chờ đọc lại. Chức năng nhập tay thông thường vẫn còn.

Lưu từng lịch thành công được đánh dấu riêng. Nếu mất kết nối/lỗi giữa chừng, bấm lưu lại để tiếp tục phần còn thiếu; mã lần lưu giúp backend không tạo trùng khi đã ghi dữ liệu nhưng phản hồi bị mất. Bản nháp chỉ nằm trong phiên trang hiện tại; tải lại trang sẽ mất phần chưa lưu.

## Công cụ và luồng

**JPG/PNG → Pillow kiểm tra/đổi chiều/loại metadata → OpenCV xử lý ảnh → Tesseract vie+eng → phân tích bố cục/cách dùng → form bản nháp → người dùng xác nhận → API lịch thuốc → worker nhắc thuốc hiện có.**

- OpenCV: phát hiện biên trang đủ rõ để chỉnh phối cảnh; xoay nghiêng dòng chữ, tăng kích thước chữ nhỏ, giảm nhiễu, cân bằng tương phản CLAHE. Thử tối đa hai cách xử lý/bố cục, chung một giới hạn thời gian.
- Tesseract: đọc chữ kèm vị trí và độ tin cậy từng từ (TSV), không phải mô hình hiểu đơn thuốc hay chẩn đoán.
- Bộ phân tích theo quy tắc: hỗ trợ đơn đánh số, bảng/cột, tên xuống dòng và một số mẫu không đánh số. Tách tên/hàm lượng, cách dùng, liều mỗi lần, đơn vị, các lần dùng, giờ ghi rõ, ngày dùng và số ngày điều trị.
- Frontend Next.js/React/TypeScript: sửa cả đơn trực tiếp trên một trang; đánh dấu ô thiếu hoặc nghi ngờ, hiện ảnh và chữ nguồn; lưu các lịch đã kiểm tra.
- Backend FastAPI/Pydantic, dữ liệu lịch PostgreSQL/SQLAlchemy; giữ nguyên luồng nhắc thuốc và phân quyền chủ nhóm hiện tại.

Ảnh được xử lý trong bộ nhớ trên máy chạy backend, không gửi sang dịch vụ OCR bên ngoài và không lưu ảnh/chữ OCR vào hồ sơ. Khi người dùng lưu, chỉ thông tin lịch đã xác nhận được ghi như lịch nhập tay.

## Quy tắc an toàn

- Hàm lượng `500 mg`, quy cách đóng gói và số lượng cấp `X 20` không được dùng để suy ra liều mỗi lần hoặc số ngày điều trị.
- Giữ riêng liều từng lần, ví dụ sáng 1 viên/tối 2 viên. Hỗ trợ số thập phân/phân số ghi rõ.
- Liều khoảng, tổng liều ngày cần chia, thời gian 7–10 ngày hoặc cách dùng khi cần phải được người dùng xác nhận; không tự chia liều hay biến thành lịch hằng ngày.
- Con số/đơn vị/giờ thiếu tin cậy được giữ trống hoặc đánh dấu. Không tự đoán tên thuốc theo danh mục.
- OCR không xác minh đơn thuộc đúng người, không kiểm tra tương tác thuốc, không thay thế chỉ định y tế. Người dùng vẫn phải đối chiếu toàn bộ đơn, kể cả ô không vàng.

## Kết quả kiểm thử và giới hạn

Ngày kiểm tra: 04/10/2026 trên máy phát triển hiện tại. **52 kiểm thử backend, 5 kiểm thử helper frontend passed; TypeScript, ESLint và production build thành công.** Kiểm tra giao diện đã thực hiện tải ảnh, điền form, xác nhận, lỗi lưu một phần và lưu lại trên API giả lập; không tạo lịch trong dữ liệu thật hoặc gửi Telegram.

Benchmark có 3 thuốc giả lập / 4 lần dùng. Mỗi lần dùng đo 6 trường: tên, cách dùng, liều, đơn vị, giờ cụ thể, ngày dùng. Các dòng bị bỏ sót vẫn nằm trong mẫu số; dữ liệu điền sai và dòng dư được báo riêng.

| Mẫu giả lập | Điền đúng | Điền sai | Bỏ trống |
| --- | ---: | ---: | ---: |
| Đánh số rõ | 22/24 (91,7%) | 0 | 2 |
| Bảng rõ | 22/24 (91,7%) | 0 | 2 |
| Không đánh số rõ | 22/24 (91,7%) | 0 | 2 |
| Lệch phối cảnh | 24/24 (100%) | 0 | 0 |
| Bảng nhiễu/mờ nhẹ | 24/24 (100%) | 0 | 0 |
| Thu nhỏ mạnh + mờ | 1/24 (4,2%) | 5 | 18 |

Khoảng 0,6–1,1 giây/mẫu, gồm tạo ảnh benchmark. Mẫu rõ thiếu hai trường vì đơn vị có độ tin cậy thấp được giữ trống. Mẫu rất mờ còn có dòng nhận nhầm, nên không thể coi kết quả là dữ liệu đúng chỉ vì có chữ.

**Đây là ảnh in giả lập, không chứng minh độ chính xác 70–80% trên mọi đơn bệnh viện.** Screenshot nhỏ của ảnh đơn cũ vẫn cho kết quả kém. Cần ảnh gốc rõ nét và bộ mẫu thực tế có đáp án để đo khả năng giảm thời gian nhập liệu. Chữ viết tay, lóa, che chữ, nhiều trang hoặc bố cục chưa hỗ trợ vẫn có thể cần nhập tay.

Tỷ lệ **điền sẵn** hiển thị trong ứng dụng chỉ đếm ô có dữ liệu trên các dòng nhận được, không phải tỷ lệ đọc đúng hay tỷ lệ nhận đủ toàn bộ đơn. Nếu dưới 70%, giao diện cảnh báo và cho chuyển ngay sang nhập tay. Không tính ngày bắt đầu/người phụ trách mặc định vào tỷ lệ OCR.

## Kiểm tra lại

Tại thư mục gốc:

```powershell
.\setup-ocr.cmd --check
.\BE\.venv\Scripts\python.exe .\BE\scripts\benchmark_prescription_ocr.py
```

Ảnh giả lập để demo: `docs/ocr-fixtures/numbered_perspective.png` (đủ thông tin), `table_clean.png` (có ô cần bổ sung), `numbered_low_resolution.png` (chất lượng thấp). Tuyệt đối không dùng dữ liệu giả lập để điều trị.

Máy khác: cài dependencies trong `BE/requirements.txt` vào môi trường Python, chạy `setup-ocr.cmd` để cài/kiểm tra Tesseract và ngôn ngữ; cài dependencies frontend theo hướng dẫn dự án. OpenCV là thư viện Python, không cần cài ứng dụng riêng.
