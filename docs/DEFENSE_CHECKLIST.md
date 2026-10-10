# Kiểm tra phần quản lý chăm sóc HealthGuard

Phạm vi: tài khoản, nhóm/lời mời, phân quyền theo hồ sơ, lịch thuốc/OCR, xác nhận và nhắc Telegram. Không triển khai AI té ngã, camera hoặc xử lý video trong đợt rà soát này.

## Luồng và quyền

**Chủ nhóm đăng ký → tạo nhóm → thêm hồ sơ → mời đúng email → người chăm sóc tham gia → chủ nhóm phân công/cấp quyền → tạo lịch thuốc → worker tạo lần uống → người có quyền phản hồi → lưu lịch sử.**

| Thao tác | Chủ nhóm | Người chăm sóc |
| --- | --- | --- |
| Quản lý thành viên/lời mời/phân công | Trong nhóm của mình | Không |
| Tạo/sửa hồ sơ và lịch thuốc, dùng OCR | Trong nhóm của mình | Không |
| Xem hồ sơ | Trong nhóm của mình | Chỉ hồ sơ được phân công |
| Xem lịch và ghi chú thuốc | Có | Cần quyền xem thuốc |
| Xem chẩn đoán | Có | Cần quyền xem chẩn đoán, độc lập với thuốc |
| Xác nhận lần dùng | Có | Cần quyền xác nhận, quyền xem thuốc; không được xác nhận lần đã giao riêng cho người khác |
| Sửa thông tin/đổi mật khẩu/liên kết Telegram | Tài khoản của mình | Tài khoản của mình |

Ẩn nút ở web không phải biện pháp bảo mật duy nhất: API luôn kiểm tra phiên, nhóm và quyền hiện tại. Mỗi tài khoản hiện chỉ thuộc một nhóm. Lời mời chưa phải phân công hồ sơ.

## Các tình huống quan trọng

- Lời mời sai email, hết hạn, đã dùng hoặc bị thu hồi: từ chối; cấp lại làm mã cũ mất hiệu lực. Không gửi email tự động — chủ nhóm sao chép liên kết và gửi trực tiếp.
- Thu hồi thành viên/phân công/quyền xác nhận: request tiếp theo và thông báo đang chờ kiểm tra lại quyền. Không xóa lịch sử phản hồi của người đó.
- Đổi mật khẩu: kiểm tra mật khẩu cũ, lưu hash, thu hồi phiên cũ; không lưu mật khẩu dạng rõ.
- Mất mạng: hiển thị lỗi và cho thử lại, không tự coi là đăng xuất. Phiên thật hết hạn thì chuyển về đăng nhập. Kết quả cũ không được ghi đè dữ liệu sau khi đổi tài khoản/hồ sơ/ngày.
- Liều chưa tới giờ: chưa được phản hồi. Một lần dùng chỉ có một phản hồi; gửi lại đúng dữ liệu không tạo bản ghi thứ hai, dữ liệu khác bị từ chối.
- Sửa lịch: tạo phiên bản mới và giữ lịch sử cũ. Nếu lần uống hôm nay đã đến giờ, thay đổi áp dụng từ ngày tiếp theo; lần đã đến giờ còn chờ vẫn dùng thông tin phiên bản cũ. Các lần tương lai chưa đến giờ có thể được tạo lại theo lịch mới. Ngừng lịch mới nhất ngừng cả các lần còn chờ thuộc phiên bản trước.
- Không phản hồi sau thời hạn: `UNCONFIRMED` = **chưa xác nhận**, không phải kết luận đã bỏ thuốc. `ADMINISTERED` = xác nhận của người thao tác, không phải chứng minh trực tiếp đã uống.
- Worker cần chạy để tạo lần uống và gửi nhắc. Khi chạy lại, có cửa sổ bù 24 giờ cho các lần chưa được tạo; các lần đã tạo trước đó tiếp tục được xử lý. Không phục dựng được mọi lần chưa từng được tạo nếu tắt máy lâu hơn cửa sổ này.
- Telegram là tùy chọn; chỉ liên kết chat riêng qua mã dùng một lần. Nhắc vào giờ dùng và sau 15/30/45 phút, báo chủ nhóm sau 60 phút chưa phản hồi theo cấu hình mặc định. Gửi lỗi được thử tối đa 3 lần, cách nhau 5 phút; mất phản hồi mạng sau khi nhà cung cấp đã nhận có thể gây gửi lặp, không hứa “exactly once”.
- OCR chỉ hỗ trợ nhập: đối chiếu đúng người, tên/hàm lượng, liều và thời gian. Không suy liều từ số lượng cấp, không tự tư vấn y khoa. Ảnh mờ/viết tay có thể cần nhập tay; xem tài liệu OCR để hiểu tỷ lệ điền ô khác độ chính xác.
- Form đang lưu không đóng bằng Escape/backdrop, không đổi người đang nhập; nếu ghi một phần thành công, giữ các lịch đó và dùng mã lần lưu để chống tạo trùng khi gửi lại.

## Demo ngắn trước buổi bảo vệ

1. Đăng nhập chủ nhóm, tạo một hồ sơ giả lập, một lịch có giờ sắp đến và một lịch khác còn xa.
2. Mời người chăm sóc bằng email khác; thử sai tài khoản rồi chuyển đúng tài khoản, tham gia nhóm.
3. Chứng minh chưa phân công thì chưa thấy hồ sơ; cấp quyền xem thuốc nhưng không xem chẩn đoán; kiểm tra bằng tài khoản người chăm sóc.
4. Cấp quyền xác nhận, tới giờ ghi “Đã cho uống” hoặc “Chưa thể cho uống” kèm lý do; chủ nhóm xem người/thời điểm phản hồi.
5. Sửa/ngừng lịch, chứng minh lịch sử cũ giữ nguyên; thu hồi quyền và kiểm tra lại.
6. Tải ảnh giả lập OCR rõ, kiểm tra ô vàng, lưu các lịch; thử ảnh mờ và chuyển nhập tay.
7. Liên kết Telegram của chủ nhóm và người chăm sóc trước demo nếu dùng kênh này. Kiểm tra mạng/tunnel, để worker chạy liên tục. Không dùng đơn/dữ liệu bệnh nhân thật để demo.

## Chạy và kiểm thử

Ứng dụng đã cấu hình: `./run-healthguard.cmd`; không cần mở 3 terminal. Không cần Telegram: `./run-healthguard.cmd --local-only`. Ctrl+C tắt các tiến trình do launcher tạo. Không chạy thêm phiên khi phiên cũ vẫn chiếm cổng.

Backend, từ `BE`:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --disable-warnings
```

Frontend, từ `FE`:

```powershell
npm test
npm run lint
npm run typecheck
npm run build
```

Kiểm thử giao diện với API thật nhưng database giả lập trong bộ nhớ, không worker/tunnel/Telegram, từ thư mục gốc:

```powershell
.\BE\.venv\Scripts\python.exe .\BE\scripts\run_ui_review.py
```

Chỉ mở ở `http://127.0.0.1:3014`; xem các tài khoản giả lập trong đầu script. Dừng script là mất dữ liệu thử. Đây không phải môi trường triển khai và không dùng với dữ liệu thật. Không chạy song song với một Next dev khác trong cùng checkout.

Kiểm thử concurrency PostgreSQL là bước riêng, opt-in: `BE/scripts/check_postgres_concurrency.py` tạo namespace thử ngẫu nhiên, không dùng bảng ứng dụng. Trên máy hiện tại role bị từ chối tạo namespace (SQLSTATE 42501), nên chưa kiểm chứng tải/khóa đồng thời trên PostgreSQL; không tự đổi quyền để chạy bài thử.

## Kết quả rà soát ngày 10/10/2026

- 99 kiểm thử backend và 21 kiểm thử frontend đạt; ESLint, TypeScript và production build thành công. Backend dùng SQLite cô lập và Telegram giả lập, không phải bài đo tải production.
- Đã thao tác qua trình duyệt với API thật/database trong bộ nhớ: chủ nhóm, caregiver, người chỉ xem; lời mời sai/đúng tài khoản; hồ sơ được phân công; quyền thuốc/chẩn đoán; phản hồi hai trạng thái; chặn liều tương lai và ghi tên/thời điểm phản hồi theo giờ Việt Nam.
- Đã kiểm tra giao diện desktop và màn hình 390×844, menu/điều hướng điện thoại. [Ảnh desktop](review-clean-ui.png), [ảnh điện thoại](review-clean-mobile.png) chỉ dùng dữ liệu giả lập.
- Không chạy gửi Telegram/webhook thật trong đợt này. Kiểm thử PostgreSQL đồng thời chưa chạy được vì thiếu quyền tạo namespace, như ghi ở trên. Cần demo Telegram thực tế và kiểm thử lại sau khi ghép AI trước buổi bảo vệ.
- Không sửa module AI, không thay dữ liệu thật, không commit/push Git. Những kết quả này không phải cam kết ứng dụng không còn bất kỳ lỗi nào.

## Tích hợp AI của thành viên khác

- Dùng chung `elder_profiles.id`, tài khoản, nhóm và phân công; không tạo bản sao hồ sơ độc lập.
- Cảnh báo AI cũng phải kiểm tra quyền ở backend, không gửi dữ liệu gia đình này sang gia đình khác.
- Chốt migration và định dạng sự kiện/endpoint với nhóm trước khi ghép nhánh. Hiện chưa có endpoint cảnh báo té ngã trong phần này; không coi ghép nhánh là đã kiểm thử tích hợp.
- Sau khi ghép, chạy lại cả test và demo hai vai trò. Không commit `.env`, bot token, dữ liệu camera hoặc ảnh bệnh nhân.
