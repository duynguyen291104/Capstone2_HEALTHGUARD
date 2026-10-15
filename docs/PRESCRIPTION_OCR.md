# Nhập đơn thuốc bằng ảnh — OCR nội bộ

HealthGuard dùng **Tesseract OCR** với dữ liệu tiếng Việt (`vie`) và tiếng Anh (`eng`). Phần đọc ảnh chạy trên máy đang chạy backend: không có API key, không gọi Google Cloud và không tính phí theo lượt. Khi triển khai lên server, Tesseract cần được cài trên server đó.

## Chạy trên máy hiện tại

Khởi động ứng dụng như trước:

```powershell
.\run-healthguard.cmd
```

Nếu ứng dụng đang chạy phiên bản cũ, nhấn Ctrl+C ở cửa sổ chạy cũ rồi chạy lại một lần. OCR không cần một terminal riêng. Internet vẫn cần cho các chức năng Telegram của launcher; bản thân OCR hoạt động offline sau khi đã cài bộ ngôn ngữ.

## Cài lần đầu trên máy Windows khác

Sau khi đã có môi trường Python của backend và cài `BE/requirements.txt`, chạy tại thư mục gốc dự án:

```powershell
.\setup-ocr.cmd
```

Script tự tìm Tesseract; nếu chưa có thì cài bằng Windows Package Manager (`winget`, gói `UB-Mannheim.TesseractOCR`). Sau đó tải hai bộ ngôn ngữ từ repository chính thức của Tesseract vào `%LOCALAPPDATA%\HealthGuardTools\tessdata`. Windows có thể hỏi quyền cài đặt. Cần Internet ở bước cài này.

Nếu máy không có winget, cài Tesseract theo [hướng dẫn Windows](https://github.com/UB-Mannheim/tesseract/wiki), sau đó chạy lại `setup-ocr.cmd`. Chọn thư mục cài riêng, không chọn thư mục gốc dự án làm nơi cài Tesseract.

Kiểm tra cài đặt mà không tải thêm gì:

```powershell
.\setup-ocr.cmd --check
```

Backend tự tìm Tesseract trong PATH và những thư mục Windows thông dụng. Nếu cài ở vị trí khác, thêm vào `BE/.env`:

```dotenv
TESSERACT_CMD=C:/duong-dan/Tesseract-OCR/tesseract.exe
TESSERACT_DATA_DIR=C:/duong-dan/tessdata
OCR_TIMEOUT_SECONDS=30
OCR_REQUESTS_PER_HOUR=20
```

Thư mục `TESSERACT_DATA_DIR` cần có cả `vie.traineddata` và `eng.traineddata`. Chỉ đặt hai đường dẫn khi cần ghi đè cơ chế tự tìm. Cấu hình Google cũ, nếu còn trong `.env`, không được đọc hay sử dụng. Không cần migration database.

Linux có thể cài bằng `sudo apt install tesseract-ocr tesseract-ocr-vie tesseract-ocr-eng`, rồi kiểm tra `tesseract --list-langs`. Không cần chạy script cài Windows.

## Cách dùng

1. Đăng nhập **chủ nhóm** → **Thuốc & lịch uống** → chọn đúng người cao tuổi.
2. Bấm **Tải ảnh đơn thuốc**, chọn JPG/PNG tối đa 5 MB, tối đa 20 megapixel. Với đơn nhiều trang, làm từng ảnh. Chưa hỗ trợ PDF/HEIC.
3. Xác nhận quyền sử dụng ảnh và đúng người trên đơn, bấm **Đọc đơn thuốc**.
4. Xem ảnh gốc, toàn bộ chữ đọc được và các dòng thuốc gợi ý. Bấm **Kiểm tra & tạo lịch**, sửa tên thuốc/hàm lượng và hướng dẫn; điền liều, đơn vị, ngày, giờ theo đơn đã được xác nhận.
5. Đối chiếu rồi tích xác nhận và bấm **Tạo lịch thuốc**. Đọc OCR không tự lưu lịch.
6. Mỗi lịch ứng với một giờ uống. Dùng **Thêm lịch theo đơn** cho giờ khác hoặc dòng thuốc bị bỏ sót. Lịch đã lưu dùng worker và cơ chế nhắc hiện có.

Ảnh mẫu giả lập để thử giao diện: [demo-prescription.png](demo-prescription.png). Đây là dữ liệu kiểm thử, không phải đơn điều trị. Nếu tạo lịch từ mẫu, dùng hồ sơ demo và ngừng lịch sau khi thử.

## Phạm vi và dữ liệu

- Ưu tiên đơn **in rõ nét**, đủ sáng, ngay ngắn. Chữ viết tay, ảnh nghiêng, mờ hoặc lóa có thể đọc sai hoặc thiếu. Chưa cam kết chất lượng trên mọi mẫu đơn bệnh viện.
- Trước khi đọc, ứng dụng phóng lớn ảnh nhỏ và tự cân độ nghiêng nhẹ của dòng chữ. Không sửa được mọi trường hợp giấy cong, góc chụp xiên hoặc chữ quá mờ.
- Bộ tách hỗ trợ dòng đánh số bằng dấu chấm/phẩy/ngoặc/gạch, tên thuốc xuống dòng và cột số lượng. Trong mục “Thuốc điều trị” có thể gợi ý thuốc không ghi hàm lượng; ngoài mục đó cần thấy hàm lượng để tránh nhận nhầm thông tin khác. OCR đọc chữ; tách dòng thuốc là quy tắc trong ứng dụng, không phải bộ kiểm chứng y khoa.
- Không tự quy đổi hàm lượng thành liều, tự đoán giờ từ “sáng/tối”, hoặc suy ra thời gian điều trị từ số viên. Nếu chỉ định không rõ, hỏi người kê đơn/nhân viên y tế.
- Ảnh được kiểm tra nội dung, bỏ EXIF/GPS và chuyển vào Tesseract qua bộ nhớ. Không ghi ảnh/chữ OCR ra file hoặc database trong luồng upload; không gửi sang nhà cung cấp OCR bên ngoài. Chỉ lưu lịch do người dùng xác nhận. Nhật ký chỉ ghi hành động và `tesseract_local`.
- Bản nháp và ảnh xem trước chỉ tồn tại trong trang đang mở; rời trang, đổi người hoặc tải lại sẽ mất bản nháp. Lịch đã lưu vẫn còn.
- Chỉ chủ nhóm được nhập đơn từ ảnh. Mỗi tài khoản mặc định có 20 lượt/giờ để hạn chế quá tải (không phải hạn mức tính tiền). Mỗi tiến trình API nhận tối đa hai tác vụ đọc chữ cùng lúc, mỗi tác vụ có timeout mặc định 30 giây.

## Kiểm thử và lỗi

Chạy kiểm thử backend:

```powershell
cd BE
.\.venv\Scripts\python.exe -m pytest -q
```

Đọc thật ảnh mẫu tiếng Việt bằng Tesseract, không truy cập dịch vụ OCR bên ngoài (chạy từ thư mục gốc):

```powershell
.\BE\.venv\Scripts\python.exe .\BE\scripts\check_ocr.py
```

Script này tạo lại ảnh mẫu giả lập tại `docs/demo-prescription.png` và kiểm tra đọc/tách hai dòng thuốc.

- **Chưa cài bộ đọc chữ/thiếu ngôn ngữ**: chạy `setup-ocr.cmd`, hoặc sửa đường dẫn trong `.env`.
- **Không tìm thấy chữ**: chụp rõ hơn, xoay ảnh ngay ngắn, tránh lóa.
- **Bộ đọc chữ đang bận**: đợi ít giây rồi thử lại.
- **Mất quá nhiều thời gian**: giảm kích thước ảnh, chụp riêng từng trang; vẫn có thể nhập thủ công.
- **Ảnh vượt giới hạn**: chọn ảnh dưới 5 MB và 20 megapixel. Backend tự chuyển ảnh sang xám, giới hạn cạnh dài 3.200 pixel trước khi đọc.

Tài liệu: [Tesseract](https://tesseract-ocr.github.io/tessdoc/), [dữ liệu ngôn ngữ chính thức](https://github.com/tesseract-ocr/tessdata_fast). Tesseract và bộ dữ liệu được cấp phép Apache-2.0.
