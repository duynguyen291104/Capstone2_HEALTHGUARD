"""Install/check the local OCR runtime and its Vietnamese/English language files."""

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app.config import get_settings
from app.errors import AppError
from app.services.prescription_ocr import check_tesseract, find_tesseract

# Fixed revision from the official Tesseract language repository.
DATA_REVISION = "87416418657359cb625c412a48b6e1d6d41c29bd"
DATA_URL = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{DATA_REVISION}"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check only; do not install/download")
    args = parser.parse_args()
    if args.check:
        command = check_tesseract()
        print(f"OCR sẵn sàng: {command[0]} — tiếng Việt + tiếng Anh, xử lý nội bộ.")
        return 0
    if os.name != "nt":
        print("Cài tesseract-ocr, tesseract-ocr-vie và tesseract-ocr-eng bằng trình quản lý gói của hệ điều hành.")
        return 1
    try:
        find_tesseract()
    except AppError:
        if get_settings().tesseract_cmd:
            raise  # A broken explicit path must be fixed, not silently ignored.
        winget = shutil.which("winget")
        if not winget:
            print("Cài Tesseract từ https://github.com/UB-Mannheim/tesseract/wiki rồi chạy lại lệnh này.")
            return 1
        print("Đang cài Tesseract bằng Windows Package Manager...", flush=True)
        result = subprocess.run([
            winget, "install", "--id", "UB-Mannheim.TesseractOCR", "--exact",
            "--source", "winget", "--silent", "--accept-package-agreements",
            "--accept-source-agreements", "--disable-interactivity",
        ], check=False)
        if result.returncode:
            print("Cài đặt chưa hoàn tất. Xem lỗi bên trên hoặc cài từ https://github.com/UB-Mannheim/tesseract/wiki")
            return 1
        find_tesseract()
    # Use a user-writable location, so downloading languages does not need admin.
    configured_data = get_settings().tesseract_data_dir
    target = Path(configured_data) if configured_data else Path(os.environ["LOCALAPPDATA"]) / "HealthGuardTools" / "tessdata"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("vie.traineddata", "eng.traineddata", "LICENSE"):
        print(f"Đang tải {name}...", flush=True)
        with urllib.request.urlopen(f"{DATA_URL}/{name}", timeout=60) as response:
            data = response.read(20 * 1024 * 1024)
        if name.endswith(".traineddata") and len(data) < 100_000:
            raise RuntimeError("Tệp ngôn ngữ tải về không hợp lệ; hãy thử lại.")
        pending = target / f"{name}.download"
        pending.write_bytes(data)
        pending.replace(target / name)
    command = check_tesseract()
    print(f"Đã sẵn sàng: {command[0]}")
    print("Tiếng Việt + tiếng Anh đã cài. Chạy run-healthguard.cmd để mở ứng dụng.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1)
    except (OSError, RuntimeError) as exc:
        print(f"Chưa hoàn tất cài OCR: {exc}", file=sys.stderr)
        raise SystemExit(1)
