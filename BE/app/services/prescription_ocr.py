"""OCR transcribes a document; conservative rules suggest editable draft rows only."""

import io
import os
import re
import shutil
import subprocess
import threading
import unicodedata
import warnings
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps, UnidentifiedImageError
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.errors import AppError

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 20_000_000
# Bound CPU work per API process. OCR never blocks the API's event loop.
_ocr_slots = threading.BoundedSemaphore(2)


class PrescriptionDraft(BaseModel):
    medication_name: str
    instructions: str
    source_text: str


class OcrResult(BaseModel):
    provider: str = "Tesseract OCR (nội bộ)"
    text: str
    drafts: list[PrescriptionDraft]
    warnings: list[str]


def deskew_angle(gray: Image.Image) -> float:
    """Estimate a small text-line tilt without relying on the paper's outline."""
    if min(gray.size) < 200:
        return 0.0
    probe = gray.copy()
    probe.thumbnail((1000, 1000))
    # Local contrast isolates ink from shadows. Central rows reduce the influence
    # of hands, backgrounds and other papers in a phone photo.
    ink = ImageChops.subtract(probe.filter(ImageFilter.GaussianBlur(4)), probe)
    ink = ink.point(lambda value: 255 if value > 22 else 0)
    width, height = ink.size
    ink = ink.crop((int(width * .12), int(height * .22), int(width * .88), int(height * .82)))
    if not ink.getbbox():
        return 0.0

    def score(angle: float) -> float:
        rotated = ink.rotate(angle, resample=Image.Resampling.BILINEAR, expand=True, fillcolor=0)
        rows = rotated.resize((1, rotated.height), Image.Resampling.BOX).tobytes()
        return sum((rows[index] - rows[index - 1]) ** 2 for index in range(1, len(rows))) / len(rows)

    coarse = max(range(-12, 13), key=score)
    angle = max((coarse + step * .25 for step in range(-3, 4)), key=score)
    # Keep straight/ambiguous images as-is instead of rotating for a tiny gain.
    return angle if abs(angle) >= .5 and score(angle) > max(1, score(0) * 1.2) else 0.0


def normalize_image(data: bytes) -> bytes:
    if not data or len(data) > MAX_BYTES:
        raise AppError(413, "IMAGE_TOO_LARGE", "Ảnh phải có dung lượng từ 1 byte đến 5 MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as original:
                if original.format not in {"JPEG", "PNG"}:
                    raise AppError(415, "INVALID_IMAGE_TYPE", "Chỉ hỗ trợ ảnh JPG và PNG")
                if original.width * original.height > MAX_PIXELS:
                    raise AppError(413, "IMAGE_TOO_LARGE", "Ảnh vượt quá 20 megapixel; hãy giảm kích thước")
                original.load()
                oriented = ImageOps.exif_transpose(original).convert("RGBA")
                clean = Image.new("RGB", oriented.size, "white")
                clean.paste(oriented, mask=oriented.getchannel("A"))
                clean = ImageOps.grayscale(clean)
                clean.thumbnail((3200, 3200), Image.Resampling.LANCZOS)
                angle = deskew_angle(clean)
                # Small phone/web images often have glyphs below 15px. Rescale
                # pixels, rather than just declaring a higher DPI to Tesseract.
                if min(clean.size) < 1200:
                    scale = min(3, 3200 / max(clean.size))
                    clean = clean.resize((round(clean.width * scale), round(clean.height * scale)), Image.Resampling.LANCZOS)
                if angle:
                    clean = clean.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
                    clean.thumbnail((3200, 3200), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                clean.save(output, format="PNG")  # New image has no GPS/EXIF metadata.
                # The 5 MB limit is for uploads, not lossless local conversion.
                return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise AppError(415, "INVALID_IMAGE", "Ảnh không hợp lệ hoặc bị hỏng") from None


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower().replace("đ", "d"))
    return "".join(char for char in value if not unicodedata.combining(char))


def draft_rows(text: str) -> list[PrescriptionDraft]:
    # Recover numbered blocks, including wrapped names and OCR comma delimiters.
    # Never infer dose from strength, quantity dispensed or surrounding numbers.
    numbered = re.compile(r"^\s*\d{1,2}\s*[.),:-]\s*(.+)$")
    strength = re.compile(r"\d\s*(?:(?:mg|mcg|µg|g|ml|IU|UI)\b|%)", re.IGNORECASE)
    quantity = re.compile(r"\s+[x×]\s*\d+\b.*$", re.IGNORECASE)
    instruction = re.compile(r"^(?:uong|wong|boi|thoa|nho|xit|tiem|ngam|nhai|cach dung|lieu dung|sang|trua|chieu|toi|ngay\s+\d)\b")
    drafts: list[PrescriptionDraft] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    in_medication_section = False
    block: list[str] = []
    block_in_section = False

    def flush() -> None:
        if not block or len(drafts) >= 30:
            return
        first = numbered.match(block[0])
        if not first:
            return
        name_lines: list[str] = []
        directions: list[str] = []
        for index, raw in enumerate(block):
            content = first.group(1) if index == 0 else raw
            folded = _fold(content).lstrip(" -–•'\"`.,")
            if instruction.match(folded):
                directions.append(content)
            elif directions:
                # A wrapped direction stays with its medication, not its name.
                if re.match(r"^(?:ngay|vao|sau|truoc|moi|lan|khi|trong|buoi|lien tuc|neu|den|khong|va)\b", folded):
                    directions.append(content)
            elif not re.match(r"^[x×]\s*\d", folded):
                name_lines.append(quantity.sub("", content).strip())
        name = " ".join(name_lines).strip()
        # Within an explicit medication section, strength may be absent (e.g.
        # vitamin products). Outside it require visible strength for a draft.
        if not name or not any(char.isalpha() for char in name) or not (block_in_section or strength.search(name)):
            return
        drafts.append(PrescriptionDraft(
            medication_name=name[:200], instructions="\n".join(directions)[:3000],
            source_text="\n".join(block)[:4000],
        ))

    for line in lines:
        folded = _fold(line).lstrip(" -–•'\"`.,")
        if re.match(r"^(?:loi\s+dan|bac\s+si|chu\s+y|tai\s+kham|hen\s+kham|da\s+tu\s+van|dung\s+thuoc\s+theo|nguoi\s+ke\s+don)\b", folded) or re.search(r"\bngay\s+\d{1,2}\s+thang\s+\d", folded):
            flush()
            block = []
            in_medication_section = False
            break
        if re.match(r"^(?:thuoc\s+dieu\s+tri|thuoc\s+va\s+cach\s+dung|ten\s+thuoc|danh\s+sach\s+thuoc)\b", folded):
            flush()
            block = []
            in_medication_section = True
            continue
        if numbered.match(line):
            flush()
            block = [line]
            block_in_section = in_medication_section
        elif block:
            block.append(line)
    flush()
    return drafts


def find_tesseract() -> str:
    configured = get_settings().tesseract_cmd
    if configured:
        path = shutil.which(configured)
        if path:
            return path
        raise AppError(503, "OCR_NOT_CONFIGURED", "Không tìm thấy Tesseract tại TESSERACT_CMD. Kiểm tra đường dẫn trong BE/.env.")
    path = shutil.which("tesseract")
    if path:
        return path
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Tesseract-OCR" / "tesseract.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "HealthGuardTools" / "Tesseract-OCR" / "tesseract.exe",
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR" / "tesseract.exe",
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Tesseract-OCR" / "tesseract.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    raise AppError(503, "OCR_NOT_CONFIGURED", "Chưa cài bộ đọc chữ. Chạy setup-ocr.cmd tại thư mục dự án rồi thử lại; bạn vẫn có thể nhập thủ công.")


def tesseract_command() -> list[str]:
    command = [find_tesseract()]
    data_dir = get_settings().tesseract_data_dir
    if not data_dir and os.name == "nt":
        local_data = Path(os.environ.get("LOCALAPPDATA", "")) / "HealthGuardTools" / "tessdata"
        if local_data.is_dir():
            data_dir = str(local_data)
    if data_dir:
        command.extend(["--tessdata-dir", data_dir])
    return command


def _run_tesseract(arguments: list[str], image: bytes | None = None) -> subprocess.CompletedProcess:
    # Use argument arrays and binary stdin: no shell or image/text files on disk.
    return subprocess.run(
        arguments, input=image, capture_output=True, check=False,
        timeout=get_settings().ocr_timeout_seconds,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        env={**os.environ, "OMP_THREAD_LIMIT": "2"},
    )


def check_tesseract() -> list[str]:
    command = tesseract_command()
    try:
        result = _run_tesseract([*command, "--list-langs"])
    except (OSError, subprocess.TimeoutExpired):
        raise AppError(503, "OCR_NOT_CONFIGURED", "Không khởi động được bộ đọc chữ. Chạy lại setup-ocr.cmd hoặc kiểm tra cấu hình Tesseract.") from None
    languages = set(result.stdout.decode("utf-8", errors="replace").splitlines())
    if result.returncode or not {"vie", "eng"}.issubset(languages):
        raise AppError(503, "OCR_LANGUAGES_MISSING", "Bộ đọc chữ thiếu tiếng Việt/tiếng Anh hoặc dữ liệu lỗi. Chạy setup-ocr.cmd để cài lại bộ ngôn ngữ.")
    return command


def _extract_text(image: bytes) -> str:
    if not _ocr_slots.acquire(blocking=False):
        raise AppError(429, "OCR_BUSY", "Bộ đọc chữ đang bận. Hãy thử lại sau ít giây.")
    try:
        command = check_tesseract()
        result = _run_tesseract([
            *command, "stdin", "stdout", "-l", "vie+eng", "--oem", "1", "--psm", "3", "--dpi", "300",
        ], image)
        if result.returncode:
            raise AppError(422, "OCR_PROCESSING_FAILED", "Không đọc được ảnh. Hãy chụp rõ và ngay ngắn rồi thử lại, hoặc nhập thủ công.")
        text = result.stdout.decode("utf-8", errors="replace").strip()
        if len(text) > 50000:
            raise AppError(422, "OCR_TOO_MUCH_TEXT", "Ảnh chứa quá nhiều nội dung; hãy tải từng trang đơn thuốc")
        if not text.strip():
            raise AppError(422, "OCR_NO_TEXT", "Không tìm thấy chữ trong ảnh; hãy chụp rõ toàn bộ đơn thuốc")
        return text
    except subprocess.TimeoutExpired:
        raise AppError(504, "OCR_TIMEOUT", "OCR mất quá nhiều thời gian. Hãy thử lại hoặc nhập thủ công") from None
    except OSError:
        raise AppError(503, "OCR_UNAVAILABLE", "Bộ đọc chữ không khởi động được. Kiểm tra cài đặt Tesseract hoặc nhập thủ công.") from None
    finally:
        _ocr_slots.release()


async def extract_prescription(image: bytes) -> OcrResult:
    text = await run_in_threadpool(_extract_text, image)
    return OcrResult(text=text, drafts=draft_rows(text), warnings=[
        "OCR có thể đọc sai. Đối chiếu tên thuốc, hàm lượng, liều và cách dùng với ảnh gốc trước khi lưu.",
        "Các dòng thuốc là bản nháp từ chữ OCR, có thể thiếu hoặc đọc sai. Kiểm tra cả thuốc uống và thuốc dùng ngoài theo đơn gốc.",
        "Ưu tiên đơn in rõ nét. Chữ viết tay, ảnh nghiêng hoặc bị lóa có thể đọc sai hoặc bỏ sót.",
        "Không tự suy ra liều hoặc giờ uống. Chỉ điền theo đơn/hướng dẫn đã được xác nhận; không rõ thì hỏi nhân viên y tế.",
    ])
