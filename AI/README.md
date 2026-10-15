# FallGuard AI

Hệ thống capstone phát hiện té ngã end-to-end từ camera:

```text
URFD RGB/video
  → MediaPipe Pose (33 khớp)
  → chuẩn hóa skeleton + velocity + geometry
  → cửa sổ causal 40 frame @ 20 FPS
  → GRU/LSTM/TCN đa nhiệm
      ├─ posture: UPRIGHT / TRANSITION / LYING
      └─ fall-event probability
  → state machine xác minh sau ngã
  → FastAPI + SQLite + webhook/Telegram + dashboard
```

Repository này đã có đủ downloader, tiền xử lý, huấn luyện, đánh giá,
video/webcam inference, API, dashboard, kiểm thử và tài liệu. Bản làm việc hiện
tại cũng đã tải và xử lý toàn bộ 70 sequence URFD camera 0 bản preview, huấn
luyện một checkpoint thật và sinh video demo có sự kiện `CONFIRMED_FALL`.

> Đây là prototype nghiên cứu, không phải thiết bị y tế và không được dùng như
> cơ chế bảo vệ hoặc gọi cấp cứu duy nhất.

## Vì sao model không dùng nhãn 8 hoạt động ngay?

URFD công khai posture label `-1/0/1`, tương ứng `UPRIGHT/TRANSITION/LYING`.
Các sequence ADL cũng có transition và deliberate lying, nên không thể đổi trực
tiếp ba giá trị này thành `NORMAL/FALLING/FALLEN`.

FallGuard dùng mô hình hai head:

- posture head học ba tư thế có nhãn thật;
- event head học sự kiện fall được dẫn xuất chỉ trong fall sequence; ADL luôn là
  event negative, kể cả khi chủ động nằm xuống.

URFD cũng không công bố mapping subject đáng tin cậy. Vì vậy split theo toàn bộ
sequence để chống frame leakage và kết quả được gọi đúng là
**cross-sequence**, không phải cross-subject. Hướng 8 activity, cross-subject và
multimodal được dành cho UP-Fall phase tiếp theo.

## Cài đặt trên Windows

PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\bootstrap.ps1
.\.venv\Scripts\Activate.ps1
fallguard doctor
```

Bootstrap tải `uv` và CPython 3.12 vào chính workspace, tạo `.venv`, khóa
dependency trong `uv.lock`, rồi cài MediaPipe, PyTorch CUDA, FastAPI và test
tools. Máy không có GPU vẫn có thể chạy CPU fallback.

Các dependency quan trọng trong môi trường đã xác minh:

- Python 3.12;
- MediaPipe Tasks 0.10.x;
- PyTorch CUDA 12.8;
- OpenCV;
- FastAPI + Uvicorn;
- NumPy, pandas và scikit-learn.

## Chạy nhanh

### 1. Smoke workflow không cần mạng

```powershell
fallguard demo --epochs 3
```

Lệnh này tạo dữ liệu skeleton synthetic, materialize window, train, evaluate và
lưu checkpoint/metrics dưới `artifacts/demo/`.

### 2. Tải URFD

Đọc license trước tại
<https://fenix.ur.edu.pl/~mkepski/ds/uf.html>. URFD dùng
CC BY-NC-SA 4.0 cho học thuật phi thương mại.

Tải toàn bộ 70 sequence camera 0 bản MP4 nhẹ:

```powershell
fallguard data download-urfd `
  --selection full `
  --profile preview `
  --accept-license `
  --no-extract
```

Profile:

- `preview`: khoảng 97 MB cho 70 camera-0 video; mỗi frame là depth + RGB ghép
  ngang, pipeline tự crop nửa RGB bên phải;
- `rgb`: khoảng 4,49 GB ZIP PNG camera 0, ảnh RGB 640×480 full resolution;
- `smoke`: chỉ một fall và một ADL để kiểm tra kết nối.

Downloader hỗ trợ resume `.part`, retry, Content-Length, ZIP CRC và safe
extraction. Server chính thức không công bố checksum nên code không giả checksum.

### 3. Trích pose và tạo dữ liệu train

```powershell
fallguard data prepare-urfd --source preview
fallguard data validate
```

Output chính:

```text
data/interim/urfd_preview/poses/*.npz
data/manifests/urfd_preview_sequences.csv
data/processed/urfd_preview/windows_train.npz
data/processed/urfd_preview/windows_validation.npz
data/processed/urfd_preview/windows_test.npz
```

Mỗi pose NPZ giữ landmarks, validity mask, timestamp, frame index, posture/event
labels và annotation provenance. Pipeline resample video 30→20 FPS, nội suy chỉ
gap ngắn, hip-center/torso-scale và tạo 239 feature/frame.

### 4. Train, calibration và test

```powershell
fallguard train `
  --data-dir data/processed/urfd_preview `
  --output artifacts/urfd_preview_gru

fallguard calibrate `
  artifacts/urfd_preview_gru/best.pt `
  --validation-data data/processed/urfd_preview `
  --output artifacts/urfd_preview_gru/calibration.json

fallguard evaluate `
  artifacts/urfd_preview_gru/best.pt `
  --data-dir data/processed/urfd_preview `
  --split test `
  --calibration artifacts/urfd_preview_gru/calibration.json `
  --output artifacts/urfd_preview_gru/test_metrics_calibrated.json
```

Calibration API từ chối nguồn có tên/metadata `test`; threshold chỉ được chọn
trên validation rồi giữ nguyên khi đánh giá test.

Artifacts:

```text
best.pt
last.pt
config.yaml
metadata.json
history.csv
metrics.json
calibration.json
test_metrics.json
test_metrics_calibrated.json
```

### 5. Suy luận video và webcam

Video thông thường:

```powershell
fallguard infer video my-video.mp4 `
  --checkpoint artifacts/urfd_preview_gru/best.pt
```

Demo trên video composite chính thức URFD:

```powershell
fallguard infer video data/raw/urfd/videos/fall-19-cam0.mp4 `
  --checkpoint artifacts/urfd_preview_gru/best.pt `
  --config configs/urfd_demo.yaml `
  --urfd-preview
```

`configs/urfd_demo.yaml` dùng thời lượng xác nhận ngắn vì clip benchmark chỉ dài
vài giây và không đủ để xác minh bất động lâu. `configs/mvp.yaml` giữ hậu kiểm
thận trọng hơn cho video thực.

Webcam:

```powershell
fallguard infer camera `
  --source 0 `
  --checkpoint artifacts/urfd_preview_gru/best.pt
```

Nhấn `Q` hoặc `Esc` để dừng.

### 6. API và dashboard

```powershell
fallguard serve `
  --checkpoint artifacts/urfd_preview_gru/best.pt `
  --open
```

Mặc định:

- dashboard: <http://127.0.0.1:8000/>;
- OpenAPI: <http://127.0.0.1:8000/docs>;
- SQLite: `runtime/fallguard.db`;
- uploaded/result media: `runtime/uploads/`, `runtime/results/`.

Endpoints:

```text
GET  /health
GET  /v1/model
POST /v1/predict/skeleton
POST /v1/predict/video
GET  /v1/events
GET  /v1/events/{event_id}
POST /v1/events/{event_id}/feedback
GET  /v1/metrics/summary
```

Webhook tùy chọn:

```powershell
$env:FALLGUARD_ALERT_WEBHOOK = "https://alert-service.example/events"
fallguard serve --checkpoint artifacts/urfd_preview_gru/best.pt
```

### Cảnh báo Telegram kèm ảnh

Khi state machine xác nhận `CONFIRMED_FALL`, FallGuard có thể gửi tin nhắn qua
Telegram; webcam/video kèm snapshot đã annotate, còn skeleton API gửi text-only.
Token bot phải được giữ bí mật. Nếu token từng được dán vào chat hoặc source,
hãy thu hồi bằng [@BotFather](https://t.me/BotFather) trước khi cấu hình token
mới.

Thiết lập nhanh bằng script tương tác; token mới được nhập ẩn và không xuất hiện
trong tham số dòng lệnh:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\configure_telegram.ps1
```

Tên/URL bot không phải Chat ID. FallGuard tự nạp `.env` từ thư mục gốc và ưu
tiên biến môi trường đang có; `.env` được loại khỏi Git, còn
[`.env.example`](.env.example) chỉ là mẫu tên biến. Không dán token mới vào
chat, source hoặc commit. Xem hướng dẫn đầy đủ, cách thủ công, nhiều người nhận,
quyền riêng tư và xử lý lỗi tại
[Cảnh báo Telegram](docs/telegram-alerts.md).

### Bộ video demo web

Chín video mẫu và catalog kết quả kỳ vọng đã được chuẩn bị trong
[`demo_videos/`](demo_videos/README.md). Mở đúng cấu hình demo bằng:

```powershell
powershell -ExecutionPolicy Bypass -File .\demo_videos\START_WEB_DEMO.ps1
```

Tên file chứa `URFD` sẽ tự bật tùy chọn crop RGB trên dashboard. Video thường
từ điện thoại/webcam không bật tùy chọn này.

## Kết quả lần chạy thật hiện tại

Thiết lập:

- URFD preview camera 0: 70 sequence;
- 7.960 frame sau resample; pose detection trung bình 86,5%;
- 795 train, 121 validation và 156 test window;
- 40 frame/window, stride 5, 239 feature/frame;
- causal GRU 2 layer, hidden 96;
- early stopping ở epoch 16, checkpoint tốt nhất epoch 9;
- threshold `0,86`, chỉ calibrate trên validation;
- test: 156 window thuộc 9 sequence, hoàn toàn tách khỏi train/validation.

| Test metric | Kết quả |
| --- | ---: |
| Posture accuracy | 0,840 |
| Posture macro F1 | 0,615 |
| Frame event precision | 0,429 |
| Frame event recall | 1,000 |
| Frame event F1 | 0,600 |
| Event-level precision | 0,600 |
| Event-level recall | 1,000 |
| Event-level F1 | 0,750 |
| False alarms/video | 0,222 |

Đây là split nhỏ với fall mô phỏng và annotation dẫn xuất; không được diễn giải
thành hiệu năng lâm sàng. Toàn bộ số liệu được sinh từ
`artifacts/urfd_preview_gru/test_metrics_calibrated.json`, không hard-code trong
logic đánh giá.

## Kiểm thử

```powershell
pytest
ruff check .
```

Test bao phủ:

- downloader resume/integrity/safe ZIP;
- parser và event derivation;
- split không group leakage;
- interpolation/normalization/causal windows;
- GRU/LSTM/TCN shape, train, checkpoint và calibration;
- state transition, inactivity, recovery và cooldown;
- video 30→20 FPS, missing-pose placeholder và URFD crop;
- FastAPI validation, SQLite, webhook, Telegram và dashboard.

## Cấu trúc chính

```text
src/fallguard/
├── data/          # URFD download, annotations, manifests, synthetic
├── pose/          # MediaPipe Tasks + resumable preparation
├── features/      # normalization, geometry, windows
├── models/        # GRU, LSTM, TCN, rule baseline, predictor
├── training/      # data loader, loss, train, metrics, calibration
├── inference/     # buffer, video/webcam, state machine
├── api/           # FastAPI, SQLite, webhook
├── notifications/ # Telegram text/photo alerts
├── web/           # responsive no-build dashboard
└── cli.py
```

Chi tiết nghiên cứu:

- [Architecture](docs/architecture.md)
- [Dataset card](docs/data-card.md)
- [Model card](docs/model-card.md)
- [Cảnh báo Telegram](docs/telegram-alerts.md)

## Dataset citation

> Bogdan Kwolek and Michal Kępski, “Human fall detection on embedded platform
> using depth maps and wireless accelerometer,” Computer Methods and Programs
> in Biomedicine, 117(3), 489–501, 2014.
> <https://doi.org/10.1016/j.cmpb.2014.09.005>
