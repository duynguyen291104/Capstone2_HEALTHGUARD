"""Generate a clearly fictional printed prescription and run real offline OCR."""

import asyncio
import io
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app.services.prescription_ocr import extract_prescription, normalize_image


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"
    font = ImageFont.truetype(str(font_path), 36)
    page = Image.new("RGB", (1800, 1200), "white")
    drawing = ImageDraw.Draw(page)
    lines = [
        "ĐƠN MẪU KIỂM THỬ OCR",
        "DỮ LIỆU GIẢ LẬP - KHÔNG DÙNG ĐIỀU TRỊ",
        "Người mẫu: Nguyễn Văn Mẫu",
        "1. Thuốc mẫu A 500mg",
        "Uống 1 viên sau ăn (dữ liệu kiểm thử)",
        "2. Thuốc mẫu B 10mg",
        "Uống 2 viên buổi tối (dữ liệu kiểm thử)",
        "Đối chiếu ảnh gốc trước khi tạo lịch.",
    ]
    for index, line in enumerate(lines):
        drawing.text((80, 80 + index * 110), line, fill="black", font=font)
    buffer = io.BytesIO()
    page.save(buffer, format="PNG")
    path = BACKEND.parent / "docs" / "demo-prescription.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buffer.getvalue())
    result = await extract_prescription(normalize_image(buffer.getvalue()))
    assert len(result.drafts) == 2, "Chưa tách đủ hai dòng thuốc trên ảnh mẫu"
    assert "500mg" in result.drafts[0].medication_name
    assert "10mg" in result.drafts[1].medication_name
    assert "Uống 1 viên" in result.drafts[0].instructions
    print(result.text)
    print(f"\nĐỌC THẬT THÀNH CÔNG: {len(result.drafts)} dòng thuốc; provider={result.provider}")
    print(f"Ảnh mẫu để thử trên giao diện: {path}")


if __name__ == "__main__":
    asyncio.run(main())
