# ĐỀ XUẤT CHỨC NĂNG NHẬP ĐƠN THUỐC TỪ ẢNH BẰNG OCR

## 1. Tên chức năng

**Nhập lịch uống thuốc từ ảnh đơn thuốc bằng OCR nội bộ**  
Tên ngắn sử dụng trong hệ thống: **Prescription OCR**.

Đây là chức năng hỗ trợ người quản lý nhóm chăm sóc tải ảnh đơn thuốc lên, nhận dạng phần chữ trong ảnh, tách các dòng thuốc thành bản nháp và chuyển sang biểu mẫu tạo lịch uống thuốc. Người dùng vẫn phải đối chiếu với ảnh gốc, chỉnh sửa và xác nhận trước khi dữ liệu được lưu.

> Cách gọi phù hợp trong proposal: **OCR hỗ trợ nhập liệu đơn thuốc**. Không nên gọi là “AI kê đơn”, “AI chẩn đoán” hoặc “AI tự quyết định liều dùng”.

## 2. Bối cảnh và vấn đề cần giải quyết

Trong quy trình chăm sóc người cao tuổi, người quản lý thường phải nhập thủ công tên thuốc, hàm lượng và hướng dẫn sử dụng từ đơn giấy. Cách nhập này có một số hạn chế:

- Mất nhiều thời gian khi đơn có nhiều loại thuốc.
- Dễ gõ sai tên thuốc hoặc bỏ sót dòng thuốc.
- Người dùng phải chuyển qua lại giữa đơn giấy và biểu mẫu nhập lịch.
- Người lớn tuổi hoặc người không quen công nghệ gặp khó khăn khi nhập nội dung dài.
- Đơn thuốc chứa dữ liệu sức khỏe nhạy cảm nên việc gửi ảnh qua một dịch vụ bên ngoài có thể phát sinh lo ngại về quyền riêng tư và chi phí sử dụng API.

Chức năng được đề xuất nhằm giảm phần nhập liệu lặp lại, nhưng vẫn giữ con người trong vòng kiểm soát để tránh việc OCR đọc sai dẫn đến tạo lịch thuốc sai.

## 3. Mục tiêu

- Cho phép nhập ảnh đơn thuốc định dạng JPG hoặc PNG ngay trên trang quản lý thuốc.
- Nhận dạng được tiếng Việt và tiếng Anh trên đơn thuốc in.
- Trích xuất các dòng thuốc có khả năng sử dụng thành bản nháp có thể chỉnh sửa.
- Hiển thị ảnh gốc song song với kết quả OCR để người dùng đối chiếu.
- Tận dụng biểu mẫu tạo lịch thuốc và cơ chế nhắc thuốc đã có của HealthGuard.
- Không tự suy đoán liều dùng, giờ uống hoặc thời gian điều trị khi đơn không ghi rõ.
- Không gửi ảnh đơn thuốc sang dịch vụ OCR bên ngoài.
- Không phát sinh phí theo số lượt nhận dạng ảnh.

## 4. Đối tượng sử dụng và phân quyền

### Người sử dụng chính

**OWNER – chủ nhóm/người quản lý chăm sóc** là người được phép:

- Chọn hồ sơ người cao tuổi.
- Tải ảnh đơn thuốc lên.
- Xem kết quả OCR và bản nháp thuốc.
- Chỉnh sửa nội dung theo đơn gốc.
- Xác nhận và tạo lịch uống thuốc.

### Người chăm sóc

**CAREGIVER – người chăm sóc** không được tự nhập đơn hoặc thay đổi lịch thuốc. Sau khi lịch đã được OWNER xác nhận và tạo, CAREGIVER được phân công mới có thể:

- Xem các lần uống thuốc cần thực hiện.
- Nhận nhắc việc theo cơ chế hiện có.
- Xác nhận đã cho uống hoặc chưa thể cho uống.

Phân quyền này giúp tách rõ người thiết lập kế hoạch thuốc và người thực hiện chăm sóc hằng ngày.

## 5. Chức năng chính

### 5.1. Tải và kiểm tra ảnh

- Chấp nhận ảnh JPG và PNG.
- Dung lượng tối đa 5 MB.
- Độ phân giải tối đa 20 megapixel.
- Người dùng phải chọn đúng hồ sơ người cao tuổi trước khi đọc ảnh.
- Người dùng phải xác nhận có quyền sử dụng ảnh và ảnh thuộc đúng người cần chăm sóc.
- Backend kiểm tra nội dung tệp thực tế, không chỉ tin vào tên mở rộng do trình duyệt gửi lên.

### 5.2. Tiền xử lý ảnh

Trước khi OCR, hệ thống sử dụng Pillow để:

- Sửa chiều ảnh dựa trên thông tin EXIF.
- Loại bỏ metadata/EXIF, bao gồm vị trí GPS nếu ảnh có chứa.
- Chuyển ảnh về định dạng màu phù hợp và ảnh xám để tăng khả năng nhận dạng.
- Phóng lớn ảnh có kích thước quá nhỏ.
- Phát hiện và cân lại độ nghiêng nhẹ của các dòng chữ.
- Giới hạn cạnh dài còn tối đa 3.200 pixel để kiểm soát thời gian và bộ nhớ xử lý.

### 5.3. Nhận dạng chữ bằng OCR

- Tesseract OCR chạy trực tiếp trên máy chủ backend.
- Sử dụng đồng thời bộ ngôn ngữ `vie` và `eng`.
- Hỗ trợ tốt nhất với đơn in rõ, đủ sáng và tương đối ngay ngắn.
- Trả về toàn bộ phần chữ đọc được để người dùng kiểm tra, kể cả khi hệ thống chưa tách được dòng thuốc.

### 5.4. Tách bản nháp thuốc

Sau khi nhận dạng chữ, bộ phân tích theo quy tắc sẽ:

- Tìm khu vực có tiêu đề như “Thuốc điều trị”, “Đơn thuốc” hoặc nội dung tương đương.
- Nhận diện các dòng thuốc được đánh số như `1.`, `2,`, `3)` hoặc `4 -`.
- Ghép tên thuốc bị xuống dòng.
- Tách phần số lượng như `X 20 viên` khỏi tên thuốc.
- Giữ lại hướng dẫn sử dụng như “uống”, “bôi”, “sáng”, “tối” dưới dạng nội dung tham khảo.
- Dừng tách khi gặp phần lời dặn hoặc thông tin bác sĩ.

Kết quả chỉ là **gợi ý/bản nháp**, không phải dữ liệu y khoa đã được xác nhận.

### 5.5. Kiểm tra và tạo lịch thuốc

- Giao diện hiển thị ảnh gốc và kết quả OCR cạnh nhau.
- Người dùng có thể sửa tên thuốc, hàm lượng và hướng dẫn.
- Người dùng tự điền liều lượng, đơn vị, ngày bắt đầu, ngày kết thúc và giờ uống theo đơn đã được xác nhận.
- Người dùng phải đánh dấu xác nhận đã đối chiếu ảnh gốc trước khi tạo lịch.
- Chỉ khi bấm **Tạo lịch thuốc**, dữ liệu mới được lưu vào hệ thống.
- Nếu một thuốc có nhiều giờ uống, người dùng tạo một lịch cho từng giờ để phù hợp cơ chế nhắc việc hiện tại.
- Nếu OCR không đọc được, người dùng vẫn có thể chọn **Thêm lịch theo đơn** để nhập thủ công.

## 6. Luồng hoạt động

```text
OWNER chọn người cao tuổi
        ↓
Chọn ảnh đơn thuốc JPG/PNG
        ↓
Xác nhận quyền sử dụng ảnh và đúng người
        ↓
Backend kiểm tra quyền, loại tệp, dung lượng và độ phân giải
        ↓
Pillow sửa chiều, bỏ metadata, làm rõ và cân nghiêng ảnh
        ↓
Tesseract nhận dạng chữ Việt + Anh trên máy chủ
        ↓
Bộ phân tích tách các dòng thuốc thành bản nháp
        ↓
Frontend hiển thị ảnh gốc + toàn bộ chữ + các thuốc gợi ý
        ↓
OWNER đối chiếu, sửa thông tin và điền lịch cụ thể
        ↓
OWNER xác nhận lần cuối
        ↓
API lịch thuốc lưu dữ liệu vào PostgreSQL
        ↓
Worker tạo lần uống và gửi nhắc qua giao diện/Telegram
        ↓
CAREGIVER xác nhận kết quả chăm sóc
```

Điểm kiểm soát quan trọng: bước OCR **không tự lưu lịch thuốc**. Nếu người dùng đóng trang hoặc không xác nhận thì chỉ có bản nháp tạm thời và không ảnh hưởng dữ liệu đang vận hành.

## 7. Công nghệ và công cụ sử dụng

| Thành phần | Công nghệ/công cụ | Vai trò |
|---|---|---|
| Giao diện | Next.js 16, React 19, TypeScript | Chọn ảnh, xem ảnh gốc, hiển thị kết quả OCR, sửa bản nháp và tạo lịch |
| API backend | FastAPI, Python | Xác thực, phân quyền, kiểm tra tệp, điều phối OCR và trả kết quả |
| Xử lý ảnh | Pillow | Sửa chiều, bỏ EXIF, chuyển ảnh xám, thay đổi kích thước và cân nghiêng |
| OCR | Tesseract OCR 5 | Nhận dạng chữ in trong ảnh |
| Ngôn ngữ OCR | `vie.traineddata`, `eng.traineddata` | Nhận dạng tiếng Việt và tiếng Anh |
| Tách thuốc | Bộ quy tắc Python | Tìm dòng đánh số, tên thuốc, hàm lượng, số lượng và hướng dẫn |
| Xử lý tác vụ | Python `subprocess` | Gọi Tesseract an toàn bằng danh sách tham số; truyền dữ liệu qua bộ nhớ |
| Mô hình dữ liệu | Pydantic | Kiểm tra và chuẩn hóa response OCR/bản nháp |
| Lưu trữ | PostgreSQL, SQLAlchemy | Lưu lịch thuốc sau khi người dùng xác nhận và lưu audit log |
| Nhắc việc | Worker hiện có của HealthGuard | Tạo lần uống, xử lý nhắc lại và trạng thái chưa xác nhận |
| Kênh thông báo | Telegram webhook hiện có | Gửi thông báo cho quy trình chăm sóc sau khi lịch được tạo |
| Cài đặt Windows | `setup-ocr.cmd`, `winget` | Cài/kiểm tra Tesseract và bộ ngôn ngữ trên máy mới |
| Chạy hệ thống | `run-healthguard.cmd` | Khởi động backend, worker, frontend và Telegram tunnel/webhook |
| Kiểm thử | Pytest, ESLint, Next.js build | Kiểm tra backend, chất lượng mã frontend và khả năng build |

Tesseract sử dụng mô hình nhận dạng đã được huấn luyện sẵn. Nhóm sinh viên không phải tự thu thập dữ liệu hoặc tự huấn luyện mô hình AI. Hệ thống cũng không cần Google Cloud Vision API hay API key trả phí.

## 8. Kiến trúc tích hợp với HealthGuard

### API OCR

```http
POST /api/v1/elders/{elder_id}/prescription-ocr?consent=true
Content-Type: image/jpeg hoặc image/png
```

Backend trả về:

- Tên bộ OCR đang sử dụng.
- Toàn bộ chữ đọc được.
- Danh sách thuốc gợi ý có thể chỉnh sửa.
- Các cảnh báo để người dùng biết cần đối chiếu ảnh gốc.

### Tích hợp với module thuốc hiện có

OCR không tạo một hệ thống lịch thuốc riêng. Sau khi kiểm tra bản nháp, giao diện gọi API tạo `medication_schedule` hiện có. Vì vậy:

- Không cần migration database riêng cho OCR.
- Không làm thay đổi lịch sử thuốc đã có.
- Dùng lại phân quyền theo nhóm chăm sóc và hồ sơ người cao tuổi.
- Dùng lại worker tạo lần uống và Telegram notification.
- Dùng lại audit log để biết ai đã tạo lịch và thời điểm thao tác.

## 9. Dữ liệu đầu vào, đầu ra và dữ liệu được lưu

| Giai đoạn | Dữ liệu | Cách xử lý |
|---|---|---|
| Đầu vào OCR | Ảnh JPG/PNG của đơn thuốc | Kiểm tra rồi xử lý trong bộ nhớ backend |
| Kết quả OCR | Toàn bộ chữ và các dòng thuốc gợi ý | Trả về trình duyệt dưới dạng bản nháp |
| Ảnh xem trước | Ảnh người dùng vừa chọn | Chỉ hiển thị trong phiên trang hiện tại |
| Khi chưa xác nhận | Ảnh, chữ OCR, bản nháp | Không lưu vào database và không lưu thành tệp trên server |
| Khi đã xác nhận | Lịch thuốc do OWNER kiểm tra | Lưu vào PostgreSQL bằng module lịch thuốc hiện có |
| Nhật ký OCR | Loại hành động và provider `tesseract_local` | Dùng để truy vết, không chứa ảnh hoặc toàn bộ nội dung đơn |

## 10. Yêu cầu chức năng

- **FR-01:** Hệ thống cho phép OWNER chọn một hồ sơ người cao tuổi trước khi tải đơn.
- **FR-02:** Hệ thống chỉ chấp nhận JPG/PNG trong giới hạn dung lượng và độ phân giải.
- **FR-03:** Hệ thống yêu cầu người dùng xác nhận quyền sử dụng ảnh.
- **FR-04:** Hệ thống kiểm tra người cao tuổi thuộc đúng nhóm chăm sóc hiện tại.
- **FR-05:** Hệ thống tiền xử lý ảnh trước khi OCR.
- **FR-06:** Hệ thống nhận dạng chữ tiếng Việt và tiếng Anh bằng Tesseract nội bộ.
- **FR-07:** Hệ thống trả toàn bộ chữ đọc được và danh sách dòng thuốc gợi ý.
- **FR-08:** Hệ thống hiển thị ảnh gốc để người dùng đối chiếu.
- **FR-09:** Người dùng được chỉnh sửa mọi trường trong bản nháp.
- **FR-10:** Hệ thống không tự tạo lịch thuốc sau khi chỉ đọc ảnh.
- **FR-11:** Chỉ lưu lịch khi người dùng xác nhận đã đối chiếu.
- **FR-12:** Khi OCR thất bại hoặc thiếu dòng, hệ thống vẫn cho nhập thủ công.
- **FR-13:** Lịch được tạo tiếp tục đi qua worker và quy trình nhắc thuốc hiện có.

## 11. Yêu cầu phi chức năng

- **Bảo mật:** endpoint yêu cầu phiên đăng nhập hợp lệ và quyền OWNER.
- **Riêng tư:** ảnh không gửi sang nhà cung cấp OCR bên ngoài và không được lưu bởi endpoint OCR.
- **Hiệu năng:** mỗi tiến trình backend xử lý tối đa hai tác vụ OCR đồng thời.
- **Chống lạm dụng:** mặc định mỗi tài khoản tối đa 20 lượt OCR trong một giờ.
- **Timeout:** mỗi lượt OCR mặc định tối đa 30 giây.
- **Khả dụng:** khi OCR không dùng được, luồng nhập lịch thủ công vẫn hoạt động.
- **Khả năng triển khai:** máy chạy backend cần cài Tesseract và hai bộ ngôn ngữ Việt/Anh.
- **Khả năng bảo trì:** OCR tách biệt với API lưu lịch, nên có thể thay hoặc nâng cấp engine sau này mà không thay đổi mô hình lịch thuốc.

## 12. Quy tắc nghiệp vụ và an toàn y tế

- OCR chỉ sao chép chữ từ ảnh, không đánh giá đơn thuốc đúng hay sai.
- Hệ thống không chẩn đoán bệnh, kê thuốc hoặc đề xuất đổi thuốc.
- Hệ thống không tự quy đổi hàm lượng thuốc thành số viên cần uống.
- Hệ thống không tự suy ra giờ chính xác chỉ từ các từ chung như “sáng” hoặc “tối”.
- Hệ thống không suy ra số ngày điều trị từ tổng số viên.
- Nội dung OCR luôn có khả năng sai; người dùng phải kiểm tra với ảnh gốc.
- Nếu đơn không rõ, người dùng cần xác nhận lại với bác sĩ/dược sĩ hoặc cơ sở y tế.
- Một lịch được tạo không có nghĩa là HealthGuard đã xác minh tính hợp lệ y khoa của đơn.

Những quy tắc trên giúp giới hạn trách nhiệm của hệ thống và tránh biến công cụ nhập liệu thành công cụ ra quyết định y tế.

## 13. Xử lý lỗi và phương án dự phòng

| Tình huống | Phản hồi của hệ thống |
|---|---|
| Sai định dạng hoặc ảnh giả mạo phần mở rộng | Từ chối tệp và thông báo chọn JPG/PNG hợp lệ |
| Ảnh quá lớn | Từ chối và hiển thị giới hạn cho người dùng |
| Không tìm thấy chữ | Đề nghị chụp lại rõ hơn hoặc nhập thủ công |
| Có chữ nhưng không tách được thuốc | Vẫn hiển thị toàn bộ chữ; cho phép thêm lịch thủ công |
| Ảnh mờ, nghiêng hoặc lóa | Cảnh báo kết quả có thể thiếu/sai; yêu cầu đối chiếu |
| Tesseract chưa được cài | Hiển thị lỗi cấu hình; quản trị chạy `setup-ocr.cmd` |
| OCR đang bận | Yêu cầu người dùng đợi rồi thử lại |
| OCR vượt quá 30 giây | Dừng tác vụ, không lưu dữ liệu và cho phép thử ảnh nhỏ hơn |
| Người dùng không có quyền | Backend trả lỗi phân quyền; không xử lý ảnh |

## 14. Phạm vi hiện tại

### Đã hỗ trợ

- Đơn thuốc in bằng tiếng Việt/Anh.
- Ảnh JPG/PNG một trang.
- Tách các dòng thuốc phổ biến theo số thứ tự.
- Hiển thị ảnh gốc, chữ OCR và bản nháp chỉnh sửa.
- Kết nối bản nháp với luồng tạo lịch, worker và Telegram hiện có.
- Chạy OCR nội bộ, không cần cloud OCR.

### Chưa nằm trong phạm vi

- Chữ viết tay khó đọc.
- PDF, HEIC hoặc nhiều trang trong một lần tải.
- Nhận diện viên thuốc từ hình dạng/màu sắc.
- Kiểm tra tương tác thuốc, dị ứng hoặc chống chỉ định.
- Tự động quyết định liều, giờ uống hay thời gian điều trị.
- Xác minh chữ ký/con dấu của cơ sở y tế.
- Cam kết đọc đúng mọi mẫu đơn của mọi bệnh viện.
- Thay thế tư vấn của bác sĩ hoặc dược sĩ.

## 15. Tiêu chí nghiệm thu

1. OWNER có thể chọn một người cao tuổi và tải ảnh JPG/PNG hợp lệ.
2. CAREGIVER hoặc người ngoài nhóm không thể gọi API OCR để tạo lịch.
3. Ảnh sai định dạng, quá 5 MB hoặc quá 20 megapixel bị từ chối.
4. Với ảnh đơn in rõ, hệ thống trả về phần chữ đã đọc và ít nhất các dòng thuốc có thể nhận diện được theo mẫu hỗ trợ.
5. Người dùng luôn nhìn thấy ảnh gốc khi kiểm tra kết quả.
6. Người dùng có thể sửa nội dung OCR trước khi tạo lịch.
7. Chỉ đọc ảnh không làm phát sinh bản ghi lịch thuốc.
8. Lịch chỉ được lưu sau bước xác nhận cuối cùng.
9. Lịch đã lưu xuất hiện trong luồng nhắc thuốc và có thể được người chăm sóc xác nhận.
10. Khi OCR thất bại, người dùng vẫn nhập lịch thủ công được.
11. Ảnh và toàn bộ chữ OCR không được lưu thành tệp hoặc bản ghi database bởi endpoint OCR.
12. Bộ kiểm thử backend, lint frontend và production build phải chạy thành công trước khi bàn giao.

## 16. Kịch bản demo đề xuất

1. Đăng nhập bằng tài khoản OWNER.
2. Mở mục **Thuốc & lịch uống** và chọn hồ sơ demo.
3. Tải một ảnh đơn thuốc in rõ.
4. Tích xác nhận quyền sử dụng ảnh và bấm **Đọc đơn thuốc**.
5. Giải thích rằng Tesseract đang chạy cục bộ, không gọi Google Cloud.
6. Chỉ vào ảnh gốc, toàn bộ chữ OCR và các dòng thuốc được tách.
7. Cố ý sửa một lỗi OCR để thể hiện cơ chế human-in-the-loop.
8. Điền liều, đơn vị, giờ và thời gian điều trị dựa trên đơn demo.
9. Xác nhận và tạo lịch.
10. Mở danh sách lịch/lần uống để chứng minh dữ liệu đã đi vào quy trình nhắc thuốc.
11. Dùng tài khoản CAREGIVER để xác nhận một lần uống nếu cần trình diễn luồng đầy đủ.

Không nên sử dụng đơn thật có thông tin cá nhân trong buổi bảo vệ. Nên dùng ảnh giả lập tại `docs/demo-prescription.png` hoặc một đơn đã che toàn bộ thông tin nhận dạng.

## 17. Giá trị của chức năng

### Giá trị cho người dùng

- Giảm thời gian gõ lại đơn thuốc.
- Giảm lỗi nhập tên thuốc do thao tác thủ công.
- Có ảnh gốc để kiểm tra ngay trong cùng màn hình.
- Duy trì được quy trình xác nhận rõ ràng trước khi lưu.

### Giá trị cho dự án

- Bổ sung một chức năng có tính ứng dụng thực tế cho module chăm sóc người cao tuổi.
- Tích hợp trực tiếp với lịch thuốc, worker, Telegram và phân quyền hiện có.
- Thể hiện cách ứng dụng OCR nhưng vẫn có kiểm soát rủi ro y tế.
- Không phụ thuộc API trả phí và không cần nhóm tự huấn luyện mô hình.
- Có thể nâng cấp từng phần mà không phải thay lại toàn bộ hệ thống.

## 18. Hạn chế và hướng phát triển

### Hạn chế

- Chất lượng phụ thuộc ánh sáng, độ nét, góc chụp và bố cục đơn.
- Tesseract có thể sai dấu tiếng Việt, tên biệt dược hoặc ký tự nhỏ.
- Bộ tách theo quy tắc chưa thể bao phủ mọi mẫu bệnh viện.
- Chữ viết tay và bảng phức tạp có độ chính xác thấp.
- Hiện tại người dùng vẫn cần tự xác định lịch cụ thể từ chỉ định đã được xác nhận.

### Hướng phát triển

- Hỗ trợ PDF và nhiều trang.
- Cho phép cắt/xoay ảnh trực tiếp trước khi đọc.
- Thêm mẫu tách riêng cho các định dạng đơn thường gặp.
- Lưu ảnh đơn có mã hóa khi có yêu cầu nghiệp vụ và chính sách đồng ý rõ ràng.
- Đo độ tin cậy theo từng vùng chữ để làm nổi bật phần cần kiểm tra.
- Xây dựng bộ dữ liệu đơn đã ẩn danh để đánh giá định lượng độ chính xác.
- Cho phép quản trị lựa chọn engine OCR khác nhưng vẫn giữ bước xác nhận của con người.

## 19. Cài đặt và cách chạy

### Cài OCR lần đầu trên một máy Windows mới

Tại thư mục gốc dự án:

```powershell
.\setup-ocr.cmd
```

Lệnh trên cài/kiểm tra Tesseract và tải bộ ngôn ngữ tiếng Việt, tiếng Anh. Chỉ cần thực hiện một lần trên mỗi máy chạy backend.

### Chạy toàn bộ dự án

```powershell
.\run-healthguard.cmd
```

Một lệnh sẽ khởi động backend, worker nhắc thuốc, frontend và tunnel/webhook Telegram. OCR không cần mở terminal riêng.

### Kiểm tra OCR độc lập

```powershell
.\BE\.venv\Scripts\python.exe .\BE\scripts\check_ocr.py
```

Tài liệu vận hành chi tiết nằm tại `docs/PRESCRIPTION_OCR.md`.

## 20. Đoạn mô tả ngắn để đưa vào proposal

> HealthGuard bổ sung chức năng nhập lịch uống thuốc từ ảnh đơn thuốc bằng công nghệ OCR nội bộ. Người quản lý tải ảnh đơn thuốc JPG/PNG, hệ thống sử dụng Pillow để tiền xử lý ảnh và Tesseract OCR với bộ ngôn ngữ Việt–Anh để nhận dạng nội dung. Kết quả được phân tích thành các dòng thuốc gợi ý và hiển thị song song với ảnh gốc. Người dùng phải đối chiếu, chỉnh sửa, bổ sung liều và thời gian, sau đó xác nhận trước khi lịch được lưu vào PostgreSQL và đưa vào quy trình nhắc thuốc của hệ thống. OCR chỉ hỗ trợ nhập liệu, không kê đơn, không chẩn đoán và không tự suy luận liều dùng. Toàn bộ quá trình nhận dạng chạy trên máy chủ HealthGuard, không gửi ảnh sang dịch vụ OCR bên ngoài, không cần API key và không phát sinh chi phí theo lượt sử dụng.

## 21. Câu trả lời ngắn khi giảng viên phản biện

### “Nếu OCR đọc sai thì sao?”

OCR không tự tạo lịch. Ảnh gốc luôn được hiển thị để OWNER đối chiếu, mọi trường đều có thể sửa và chỉ lưu khi người dùng xác nhận lần cuối. Nếu không đọc được thì chuyển sang nhập thủ công.

### “Tại sao không để AI tự điền liều và giờ?”

Vì đây là dữ liệu y tế có rủi ro cao. Cụm từ trên đơn có thể mơ hồ và OCR cũng có thể sai. Hệ thống chủ động không suy đoán, chỉ hỗ trợ sao chép nội dung và để người có trách nhiệm xác nhận.

### “Có phải nhóm tự train AI không?”

Không. Tesseract dùng mô hình OCR đã được huấn luyện sẵn cho tiếng Việt và tiếng Anh. Phần nhóm xây dựng là quy trình xử lý ảnh, tích hợp backend/frontend, phân quyền, tách bản nháp và bước kiểm tra an toàn.

### “Tại sao không dùng Google Cloud Vision?”

Phiên bản hiện tại ưu tiên Tesseract chạy nội bộ để không phụ thuộc tài khoản thanh toán, không phát sinh phí theo lượt và hạn chế việc gửi dữ liệu sức khỏe ra ngoài. Kiến trúc vẫn cho phép thay engine OCR trong tương lai.

### “Ảnh có được lưu lại không?”

Endpoint OCR chỉ xử lý ảnh trong bộ nhớ, bỏ metadata và trả bản nháp về trình duyệt. Ảnh và toàn bộ chữ OCR không được lưu vào database; chỉ lịch thuốc đã được OWNER kiểm tra và xác nhận mới được lưu.

### “Chức năng này khác nhập thủ công ở đâu?”

Nhập thủ công yêu cầu gõ lại toàn bộ tên thuốc và hướng dẫn. OCR tạo sẵn bản nháp từ ảnh để giảm thao tác, nhưng vẫn giữ biểu mẫu chỉnh sửa và bước xác nhận. Vì vậy chức năng tăng tốc nhập liệu mà không loại bỏ kiểm soát của con người.
