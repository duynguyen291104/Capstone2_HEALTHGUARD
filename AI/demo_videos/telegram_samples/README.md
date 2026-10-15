# Ảnh mẫu gửi thử Telegram

`01_FALL_CONFIRMED_snapshot.jpg` là khung hình được FallGuard trích tự động từ
video demo số 01 tại thời điểm state machine xác nhận `CONFIRMED_FALL`. Ảnh đã
có skeleton, trạng thái, tư thế và xác suất té ngã.

Sau khi đặt token mới và Chat ID trong cùng cửa sổ PowerShell, gửi thử bằng:

```powershell
fallguard telegram test `
  --snapshot .\demo_videos\telegram_samples\01_FALL_CONFIRMED_snapshot.jpg
```

Ảnh được dẫn xuất từ UR Fall Detection Dataset (URFD), chỉ dùng cho mục đích
học thuật phi thương mại theo CC BY-NC-SA 4.0.
