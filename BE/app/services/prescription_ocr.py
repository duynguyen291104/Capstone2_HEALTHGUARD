"""Local OCR produces confidence-marked editable drafts; it never saves schedules."""

import csv
import io
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import warnings
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageChops, ImageFilter, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.errors import AppError

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 20_000_000
_ocr_slots = threading.BoundedSemaphore(2)
cv2.setNumThreads(2)


class FieldReview(BaseModel):
    field: str
    reason: str
    confidence: float | None = None


class Administration(BaseModel):
    label: str
    dose_amount: float | None = None
    dose_unit: str | None = None
    time_of_day: str | None = None


class PrescriptionDraft(BaseModel):
    medication_name: str
    instructions: str
    source_text: str
    dose_amount: float | None = None
    dose_unit: str | None = None
    administrations: list[Administration] = Field(default_factory=list)
    days_of_week: list[int] | None = None
    duration_days: int | None = None
    confidence: float | None = None
    field_reviews: list[FieldReview] = Field(default_factory=list)


class OcrQuality(BaseModel):
    recognized_medications: int = 0
    prefilled_fields: int = 0
    total_fields: int = 0
    coverage_percent: int = 0
    low_confidence_fields: int = 0
    elapsed_ms: int = 0
    preprocessing: list[str] = Field(default_factory=list)


class OcrResult(BaseModel):
    provider: str = "Tesseract OCR (nội bộ)"
    text: str
    drafts: list[PrescriptionDraft]
    warnings: list[str]
    quality: OcrQuality = Field(default_factory=OcrQuality)


@dataclass
class OcrWord:
    text: str
    confidence: float
    left: int
    top: int
    width: int
    height: int


@dataclass
class OcrLine:
    text: str
    words: list[OcrWord]


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower().replace("đ", "d"))
    return "".join(char for char in value if not unicodedata.combining(char))


def deskew_angle(gray: Image.Image) -> float:
    """Estimate small text-line tilt; blank/ambiguous pages stay unchanged."""
    if min(gray.size) < 200:
        return 0.0
    probe = gray.copy()
    probe.thumbnail((1000, 1000))
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
    return angle if abs(angle) >= .5 and score(angle) > max(1, score(0) * 1.2) else 0.0


def normalize_image(data: bytes) -> bytes:
    """Validate uploads, correct EXIF orientation and strip all metadata."""
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
                clean.thumbnail((3200, 3200), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                clean.save(output, format="PNG")
                return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise AppError(415, "INVALID_IMAGE", "Ảnh không hợp lệ hoặc bị hỏng") from None


def rectify_document(gray: np.ndarray) -> tuple[np.ndarray, bool]:
    """Warp only a large, convex, bright paper boundary, never a text block."""
    height, width = gray.shape
    if min(height, width) < 300:
        return gray, False
    scale = min(1.0, 1000 / max(height, width))
    probe = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    edges = cv2.Canny(cv2.GaussianBlur(probe, (5, 5), 0), 35, 100)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area = probe.shape[0] * probe.shape[1]
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
        if not .40 <= cv2.contourArea(contour) / area <= .97:
            continue
        polygon = cv2.approxPolyDP(contour, .02 * cv2.arcLength(contour, True), True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            continue
        mask = np.zeros(probe.shape, np.uint8)
        cv2.fillConvexPoly(mask, polygon.reshape(4, 2), 255)
        inside, outside = probe[mask == 255], probe[mask == 0]
        if outside.size < 100 or float(np.median(inside)) < float(np.median(outside)) + 12:
            continue
        points = polygon.reshape(4, 2).astype(np.float32) / scale
        ordered = np.array([points[np.argmin(points.sum(axis=1))], points[np.argmin(np.diff(points, axis=1).ravel())], points[np.argmax(points.sum(axis=1))], points[np.argmax(np.diff(points, axis=1).ravel())]], dtype=np.float32)
        if len(np.unique(ordered, axis=0)) != 4:
            continue
        corner_cosines = []
        for index in range(4):
            left, right = ordered[(index - 1) % 4] - ordered[index], ordered[(index + 1) % 4] - ordered[index]
            corner_cosines.append(abs(float(np.dot(left, right) / (np.linalg.norm(left) * np.linalg.norm(right)))))
        if max(corner_cosines) > .65:
            continue
        target_width = round(max(np.linalg.norm(ordered[1] - ordered[0]), np.linalg.norm(ordered[2] - ordered[3])))
        target_height = round(max(np.linalg.norm(ordered[3] - ordered[0]), np.linalg.norm(ordered[2] - ordered[1])))
        if min(target_width, target_height) < 250:
            continue
        target = np.array([[0, 0], [target_width - 1, 0], [target_width - 1, target_height - 1], [0, target_height - 1]], np.float32)
        return cv2.warpPerspective(gray, cv2.getPerspectiveTransform(ordered, target), (target_width, target_height), borderValue=255), True
    return gray, False


def prepare_ocr_images(image: bytes) -> tuple[list[tuple[bytes, int]], list[str]]:
    """At most two printed-document candidates, processed entirely in memory."""
    decoded = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_GRAYSCALE)
    if decoded is None or decoded.size > MAX_PIXELS:
        raise AppError(415, "INVALID_IMAGE", "Ảnh không hợp lệ hoặc bị hỏng")
    steps = ["Loại metadata; chỉnh chiều ảnh", "OpenCV: ảnh xám"]
    gray, rectified = rectify_document(decoded)
    if rectified:
        steps.append("OpenCV: chỉnh phối cảnh trang giấy")
    height, width = gray.shape
    scale = min(3.0, 3200 / max(height, width)) if min(height, width) < 1200 else min(1.0, 3200 / max(height, width))
    if scale != 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
        steps.append("OpenCV: tăng kích thước chữ nhỏ" if scale > 1 else "OpenCV: giới hạn kích thước xử lý")
    angle = deskew_angle(Image.fromarray(gray))
    if angle:
        gray = np.asarray(Image.fromarray(gray).rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255))
        if max(gray.shape) > 3200:
            resize_scale = 3200 / max(gray.shape)
            gray = cv2.resize(gray, None, fx=resize_scale, fy=resize_scale, interpolation=cv2.INTER_AREA)
        steps.append(f"Chỉnh nghiêng dòng chữ ({angle:+.1f}°)")
    denoised = cv2.medianBlur(gray, 3)
    contrasted = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(denoised)
    threshold = cv2.adaptiveThreshold(contrasted, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 15)
    steps.append("OpenCV: giảm nhiễu, cân bằng tương phản CLAHE")
    return [(cv2.imencode(".png", contrasted)[1].tobytes(), 3), (cv2.imencode(".png", threshold)[1].tobytes(), 6)], steps


def parse_tsv(output: str) -> list[OcrLine]:
    """Keep boxes/confidence and reunite table columns on the same visual row."""
    if not output.startswith("level\t"):
        return [OcrLine(line.strip(), []) for line in output.splitlines() if line.strip()]
    words: list[OcrWord] = []
    for row in csv.DictReader(io.StringIO(output), delimiter="\t", quoting=csv.QUOTE_NONE):
        try:
            value = row.get("text", "").strip()
            confidence = float(row["conf"])
            if row.get("level") != "5" or not value or confidence < 0:
                continue
            words.append(OcrWord(value, min(100, confidence), int(row["left"]), int(row["top"]), int(row["width"]), int(row["height"])))
        except (KeyError, ValueError, TypeError):
            continue
    if sum(len(word.text) for word in words) > 50000:
        raise AppError(422, "OCR_TOO_MUCH_TEXT", "Ảnh chứa quá nhiều nội dung; hãy tải từng trang đơn thuốc")
    rows: list[list[OcrWord]] = []
    for word in sorted(words, key=lambda item: (item.top + item.height / 2, item.left)):
        center = word.top + word.height / 2
        matches = [row for row in rows[-3:] if abs(center - float(np.median([w.top + w.height / 2 for w in row]))) <= max(4, float(np.median([w.height for w in row])) * .50)]
        if matches:
            min(matches, key=lambda row: abs(center - float(np.median([w.top + w.height / 2 for w in row])))).append(word)
        else:
            rows.append([word])
    result = []
    for row in rows:
        row.sort(key=lambda word: word.left)
        result.append(OcrLine(" ".join(word.text for word in row), row))
    return result


_NUMBERED = re.compile(r"^\s*\d{1,2}\s*(?:[.),:-]\s*|[|]\s*|\s+(?=[A-Za-zÀ-ỹ]))(.+)$")
_STRENGTH = re.compile(r"\d\s*(?:(?:mg|mcg|µg|g|ml|IU|UI)\b|%)", re.IGNORECASE)
_INSTRUCTION = re.compile(r"\b(?:uong|wong|boi|thoa|nho|xit|tiem|ngam|nhai|cach dung|lieu dung|sang|trua|chieu|toi|ngay\s+\d|moi\s+lan|lan\s+uong)\b")
_STOP = re.compile(r"^(?:loi\s+dan|bac\s+si|chu\s+y|tai\s+kham|hen\s+kham|da\s+tu\s+van|dung\s+thuoc\s+theo|nguoi\s+ke\s+don|chan\s+doan|benh\s+nhan|doi\s+chieu\s+anh)\b")
_HEADING = re.compile(r"^(?:thuoc\s+dieu\s+tri|thuoc\s+va\s+cach\s+dung|ten\s+thuoc|danh\s+sach\s+thuoc|stt\s+ten\s+thuoc|thuoc\s+ham\s+luong)\b")
_UNIT = r"(?:vien|viên|ống|ong|ml|giọt|giot|gói|goi|muỗng|muong|lần xịt|lan xit|nhát|nhat|mg|mcg|µg|g|iu|ui)"
_DOSE = re.compile(rf"(?<![\d/.,-])(\d+(?:[.,]\d+)?|1/2|1/4|3/4)\s*({_UNIT})\b", re.IGNORECASE)
_PERIOD = re.compile(r"\b(?:buoi\s+)?(sang|trua|chieu|toi)\b(?!\s+(?:da|thieu|uu)\b)")
_LABELS = {"sang": "Sáng", "trua": "Trưa", "chieu": "Chiều", "toi": "Tối"}
_CLOCK = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*(?::\s*([0-5]\d)(?!\d)|h(?:\s*([0-5]\d))?\b|gio\b)")
_FREQUENCY = re.compile(r"\b(?:ngay\s*(\d+)\s*lan|(\d+)\s*lan\s*(?:/\s*ngay|moi\s+ngay|mot\s+ngay))\b")
_DURATION = re.compile(r"\b(?:trong|lien tuc|dung|uong)\s+(\d{1,3})\s*ngay\b")


def _instruction_match(value: str) -> re.Match | None:
    """Route/form words inside a medicine name are not dosage directions."""
    folded = _fold(value)
    for match in _INSTRUCTION.finditer(folded):
        prefix = folded[:match.start()]
        if prefix.count("(") > prefix.count(")"):
            continue
        if match.start() and match.group() in {"uong", "wong", "boi", "thoa", "nho", "xit", "tiem", "ngam", "nhai"}:
            after = folded[match.end():].lstrip()
            if not re.match(r"^(?:\d|ngay\b|sang\b|trua\b|chieu\b|toi\b|moi\b|lan\b|vao\b|truoc\b|sau\b|theo\b|khi\b)", after):
                continue
        return match
    return None


def _time_value(match: re.Match) -> str:
    return f"{int(match.group(1)):02d}:{match.group(2) or match.group(3) or '00'}"


def _span_words(line: OcrLine, start: int, end: int) -> list[OcrWord]:
    words = []
    offset = 0
    for word in line.words:
        if offset < end and offset + len(word.text) > start:
            words.append(word)
        offset += len(word.text) + 1
    return words


def _number(value: str) -> float:
    if "/" in value:
        numerator, denominator = value.split("/")
        return float(numerator) / float(denominator)
    return float(value.replace(",", "."))


def _dose_candidates(value: str) -> list[tuple[float, str]]:
    value = re.sub(r"\d+(?:[.,]\d+)?\s*(?:[-–]|đến|den|hoặc|hoac)\s*\d+(?:[.,]\d+)?\s*" + _UNIT, "", value, flags=re.IGNORECASE)
    candidates = []
    for match in _DOSE.finditer(value):
        amount = _number(match.group(1))
        if 0 < amount <= 10000:
            unit = _fold(match.group(2))
            unit = {"vien": "viên", "ong": "ống", "giot": "giọt", "goi": "gói", "muong": "muỗng", "lan xit": "lần xịt", "nhat": "nhát"}.get(unit, unit)
            candidates.append((amount, unit))
    return candidates


def _extract_usage(instructions: str) -> tuple[float | None, str | None, list[Administration], list[int] | None, int | None, list[FieldReview]]:
    folded = _fold(instructions)
    reviews: list[FieldReview] = []
    periods = list(_PERIOD.finditer(folded))
    hour_matches = list(_CLOCK.finditer(folded))
    administrations: list[Administration] = []
    all_doses = _dose_candidates(instructions)
    ambiguous_total = bool(re.search(r"\b(?:chia\s*(?:lam\s*)?\d+\s*lan|tong\s*lieu|lieu\s*ca\s*ngay)\b", folded))
    conditional = bool(re.search(r"\b(?:khi can|khi dau|neu|tuy|theo chi dinh|tang lieu|giam lieu)\b", folded))
    common = all_doses[0] if all_doses and len(set(all_doses)) == 1 and not ambiguous_total else None
    dose_before_period = bool(periods and _dose_candidates(instructions[:periods[0].start()]) and len(set(all_doses)) > 1)
    if periods:
        for index, match in enumerate(periods):
            end = periods[index + 1].start() if index + 1 < len(periods) else len(instructions)
            before_start = periods[index - 1].end() if index else 0
            segment_doses = _dose_candidates(instructions[before_start:match.start()] if dose_before_period else instructions[match.end():end])
            if not segment_doses and dose_before_period:
                segment_doses = _dose_candidates(instructions[match.end():end])
            dose = segment_doses[0] if segment_doses and len(set(segment_doses)) == 1 and not ambiguous_total else common
            explicit = next((hour for hour in hour_matches if match.start() <= hour.start() < end), None)
            administrations.append(Administration(label=_LABELS[match.group(1)], dose_amount=dose[0] if dose else None, dose_unit=dose[1] if dose else None, time_of_day=_time_value(explicit) if explicit else None))
    elif hour_matches:
        for match in hour_matches:
            administrations.append(Administration(label=_time_value(match), dose_amount=common[0] if common else None, dose_unit=common[1] if common else None, time_of_day=_time_value(match)))
    frequency = _FREQUENCY.search(folded)
    if not administrations and frequency and not conditional and 1 <= int(frequency.group(1) or frequency.group(2)) <= 6:
        for index in range(int(frequency.group(1) or frequency.group(2))):
            administrations.append(Administration(label=f"Lần {index + 1}", dose_amount=common[0] if common else None, dose_unit=common[1] if common else None))
    if not administrations:
        administrations = [Administration(label="Lịch dùng", dose_amount=common[0] if common else None, dose_unit=common[1] if common else None)]
    if frequency and len(administrations) != int(frequency.group(1) or frequency.group(2)):
        reviews.append(FieldReview(field="administrations", reason="Số lần/ngày và các buổi ghi trong đơn không khớp; kiểm tra ảnh gốc."))
    if any(item.time_of_day is None for item in administrations):
        reviews.append(FieldReview(field="time_of_day", reason="Đơn chưa có giờ cụ thể; chọn giờ theo hướng dẫn đã xác nhận."))
    if not common:
        reason = "Liều khác nhau giữa các lần dùng; kiểm tra từng lịch bên dưới." if all_doses and len(set(all_doses)) > 1 else "Chưa đọc được liều mỗi lần rõ ràng hoặc liều là khoảng/tổng ngày; cần nhập theo đơn."
        reviews.append(FieldReview(field="dose_amount", reason=reason))
    weekdays = None
    if frequency or re.search(r"\b(?:hang ngay|moi ngay|ngay (?:uong|dung)|uong ngay)\b", folded):
        weekdays = list(range(7))
    named_days = re.findall(r"\bthu\s*([2-7])\b", folded)
    if named_days or re.search(r"\bchu nhat\b", folded):
        weekdays = sorted({int(day) - 2 for day in named_days} | ({6} if "chu nhat" in folded else set()))
    if conditional or re.search(r"\b(?:cach ngay|ngay chan|ngay le)\b", folded):
        weekdays = None
        reviews.append(FieldReview(field="days_of_week", reason="Cách dùng có điều kiện/thay đổi; lịch cố định cần được xác nhận riêng."))
    elif weekdays is None:
        reviews.append(FieldReview(field="days_of_week", reason="Chưa xác định được các ngày dùng thuốc; đối chiếu đơn và chọn ngày."))
    duration_matches = _DURATION.findall(folded)
    duration = int(duration_matches[0]) if duration_matches and len(set(duration_matches)) == 1 and 0 < int(duration_matches[0]) <= 365 else None
    if re.search(r"\b\d+\s*(?:[-–]|den|hoac)\s*\d+\s*ngay\b", folded):
        duration = None
        reviews.append(FieldReview(field="duration_days", reason="Thời gian dùng là một khoảng; cần xác nhận ngày kết thúc."))
    return common[0] if common else None, common[1] if common else None, administrations, weekdays, duration, reviews


def draft_rows(text: str, lines: list[OcrLine] | None = None) -> list[PrescriptionDraft]:
    """Recover different printed layouts; quantities/strength are never doses."""
    records = lines if lines is not None else [OcrLine(line.strip(), []) for line in text.splitlines() if line.strip()]
    drafts: list[PrescriptionDraft] = []
    section = False
    block: list[OcrLine] = []
    block_section = False

    def flush() -> None:
        if not block or len(drafts) >= 30:
            return
        numbered = _NUMBERED.match(block[0].text)
        names: list[str] = []
        directions: list[str] = []
        for index, line in enumerate(block):
            content = (numbered.group(1) if index == 0 and numbered else line.text).strip(" |•\t")
            folded = _fold(content)
            instruction = _instruction_match(content)
            if instruction:
                before, after = content[:instruction.start()].strip(" |:-"), content[instruction.start():].strip()
                if before and not directions:
                    names.append(re.sub(r"\s+[x×]\s*\d+\b.*$", "", before, flags=re.IGNORECASE))
                directions.append(after)
            elif directions:
                if re.match(r"^(?:ngay|hang|vao|sau|truoc|moi|lan|khi|trong|buoi|lien tuc|neu|den|khong|va)\b", folded):
                    directions.append(content)
            elif not re.match(r"^[x×]\s*\d", folded):
                names.append(re.sub(r"\s+[x×]\s*\d+\b.*$", "", content, flags=re.IGNORECASE))
        name = " ".join(names)
        name = re.sub(r"\s+[x×]\s*\d+\b.*$", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\s*[|]\s*\d+(?:[.,]\d+)?\s*" + _UNIT + r"\b.*$", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\s+\d+\s*(?:viên|vien|ống|ong|gói|goi|tuýp|tuyp|chai|lọ|lo)\s*$", "", name, flags=re.IGNORECASE).strip(" |")
        if not name or not any(char.isalpha() for char in name) or not (block_section or _STRENGTH.search(name)):
            return
        if re.match(r"^(?:loi dan|benh nhan|chan doan|tai kham|uong|lieu)\b", _fold(name)):
            return
        instructions = "\n".join(directions)[:3000]
        amount, unit, administrations, weekdays, duration, reviews = _extract_usage(instructions)
        words = [word for line in block for word in line.words]
        confidence = round(sum(word.confidence * len(word.text) for word in words) / max(1, sum(len(word.text) for word in words)), 1) if words else None
        name_tokens = set(re.findall(r"[\w]+(?:[.,]\d+)?", _fold(name)))
        name_words = [word for word in words if _fold(word.text).strip(".,:;()") in name_tokens]
        name_confidence = round(sum(word.confidence for word in name_words) / len(name_words), 1) if name_words else confidence
        if name_confidence is None:
            reviews.append(FieldReview(field="medication_name", reason="Không có điểm tin cậy từ OCR; đối chiếu tên và hàm lượng."))
        elif name_confidence < 80 or any(word.confidence < 55 for word in name_words):
            reviews.append(FieldReview(field="medication_name", reason="Một phần tên/hàm lượng có độ tin cậy thấp; đối chiếu ảnh gốc.", confidence=name_confidence))
        if not instructions:
            reviews.append(FieldReview(field="instructions", reason="Chưa đọc được hướng dẫn dùng; kiểm tra phần tương ứng trên ảnh."))
        elif confidence is not None and confidence < 80:
            reviews.append(FieldReview(field="instructions", reason="Hướng dẫn có chữ đọc chưa chắc chắn; kiểm tra liều và số lần.", confidence=confidence))
        # Check the weakest relevant token for EACH numeric field. A high line
        # average must not conceal an uncertain digit or mg/g dosage unit.
        weak_fields: dict[str, list[OcrWord]] = {}
        for line in block:
            position = _instruction_match(line.text)
            if position:
                for match in _DOSE.finditer(line.text):
                    if match.start() >= position.start():
                        for field, group in (("dose_amount", 1), ("dose_unit", 2)):
                            weak_fields.setdefault(field, []).extend(word for word in _span_words(line, match.start(group), match.end(group)) if word.confidence < 80)
                folded_line = _fold(line.text)
                for field, pattern in (("time_of_day", _CLOCK), ("administrations", _FREQUENCY), ("duration_days", _DURATION), ("days_of_week", re.compile(r"\bthu\s*([2-7])\b"))):
                    for match in pattern.finditer(folded_line):
                        if match.start() >= position.start():
                            weak_fields.setdefault(field, []).extend(word for word in _span_words(line, match.start(), match.end()) if word.confidence < 80)
        for field, weak_words in weak_fields.items():
            if weak_words:
                reviews.append(FieldReview(field=field, reason="Con số/đơn vị OCR trong mục này có độ tin cậy thấp; đối chiếu ảnh rồi nhập lại.", confidence=min(word.confidence for word in weak_words)))
        if weak_fields.get("dose_amount") or weak_fields.get("dose_unit"):
            amount, unit = None, None
            for item in administrations:
                item.dose_amount, item.dose_unit = None, None
        if weak_fields.get("time_of_day"):
            for item in administrations:
                item.time_of_day = None
        if weak_fields.get("duration_days"):
            duration = None
        if weak_fields.get("days_of_week") or weak_fields.get("administrations"):
            weekdays = None
        if weak_fields.get("administrations") and not _PERIOD.search(_fold(instructions)) and not _CLOCK.search(_fold(instructions)):
            administrations = [Administration(label="Lịch dùng", dose_amount=amount, dose_unit=unit)]
        drafts.append(PrescriptionDraft(medication_name=name[:200], instructions=instructions, source_text="\n".join(line.text for line in block)[:4000], dose_amount=amount, dose_unit=unit, administrations=administrations, days_of_week=weekdays, duration_days=duration, confidence=confidence, field_reviews=reviews))

    for record_index, record in enumerate(records):
        line = record.text.strip()
        folded = _fold(line).lstrip(" -–•'\"`.,|")
        dated = bool(re.search(r"\bngay\s+\d{1,2}\s+thang\s+\d", folded))
        if not block and not drafts and (dated or re.match(r"^(?:chan doan|benh nhan)\b", folded)):
            continue
        if _STOP.match(folded) or dated:
            flush()
            block = []
            section = False
            break
        if _HEADING.match(folded):
            flush()
            block = []
            section = True
            continue
        if re.match(r"^(?:stt|so luong|don vi tinh|cach dung|lieu dung)\s*$", folded):
            continue
        numbered = _NUMBERED.match(line)
        instruction = _instruction_match(line)
        following = records[record_index + 1].text if record_index + 1 < len(records) else ""
        following_instruction = _instruction_match(following)
        continuation_expected = section and (_STRENGTH.search(following) or (following_instruction and following_instruction.start() == 0))
        looks_like_daily_direction = re.search(r"\bhang ngay\b", folded) and not _STRENGTH.search(line)
        unnumbered = bool(_STRENGTH.search(line) or continuation_expected) and not (instruction and instruction.start() == 0) and not looks_like_daily_direction and bool(re.match(r"^[A-Za-zÀ-ỹ]", line))
        if numbered or unnumbered:
            content = " ".join(item.text for item in block)
            continuation = bool(block and not numbered and (line.startswith("(") or ("(" in content and ")" not in content)))
            if not continuation:
                flush()
                block = [OcrLine(line, record.words)]
                block_section = section
            else:
                block.append(record)
        elif block:
            block.append(record)
    flush()
    return drafts


def quality_for(drafts: list[PrescriptionDraft], elapsed_ms: int = 0, preprocessing: list[str] | None = None) -> OcrQuality:
    # Six form fields per administration: name, directions, dose, unit, exact
    # hour, weekdays. This measures fill coverage, never reading correctness.
    filled = total = 0
    for draft in drafts:
        for item in draft.administrations or [Administration(label="Lịch dùng")]:
            values = [draft.medication_name, draft.instructions, item.dose_amount, item.dose_unit, item.time_of_day, draft.days_of_week]
            total += len(values)
            filled += sum(value is not None and value != "" and value != [] for value in values)
    return OcrQuality(recognized_medications=len(drafts), prefilled_fields=filled, total_fields=total, coverage_percent=round(filled / total * 100) if total else 0, low_confidence_fields=sum(len(draft.field_reviews) for draft in drafts), elapsed_ms=elapsed_ms, preprocessing=preprocessing or [])


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
        candidates = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe", Path(os.environ.get("LOCALAPPDATA", "")) / "Tesseract-OCR" / "tesseract.exe", Path(os.environ.get("LOCALAPPDATA", "")) / "HealthGuardTools" / "Tesseract-OCR" / "tesseract.exe", Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR" / "tesseract.exe", Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Tesseract-OCR" / "tesseract.exe"]
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


def _run_tesseract(arguments: list[str], image: bytes | None = None, timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, input=image, capture_output=True, check=False, timeout=timeout if timeout is not None else get_settings().ocr_timeout_seconds, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, env={**os.environ, "OMP_THREAD_LIMIT": "2"})


def check_tesseract(timeout: float | None = None) -> list[str]:
    command = tesseract_command()
    try:
        result = _run_tesseract([*command, "--list-langs"], timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise AppError(503, "OCR_NOT_CONFIGURED", "Không khởi động được bộ đọc chữ. Chạy lại setup-ocr.cmd hoặc kiểm tra cấu hình Tesseract.") from None
    languages = set(result.stdout.decode("utf-8", errors="replace").splitlines())
    if result.returncode or not {"vie", "eng"}.issubset(languages):
        raise AppError(503, "OCR_LANGUAGES_MISSING", "Bộ đọc chữ thiếu tiếng Việt/tiếng Anh hoặc dữ liệu lỗi. Chạy setup-ocr.cmd để cài lại bộ ngôn ngữ.")
    return command


def _extract_document(image: bytes) -> OcrResult:
    if not _ocr_slots.acquire(blocking=False):
        raise AppError(429, "OCR_BUSY", "Bộ đọc chữ đang bận. Hãy thử lại sau ít giây.")
    started = time.monotonic()
    deadline = started + get_settings().ocr_timeout_seconds
    try:
        command = check_tesseract(timeout=min(3, max(.1, deadline - time.monotonic())))
        candidates, steps = prepare_ocr_images(image)
        best: tuple[str, list[PrescriptionDraft]] | None = None
        timed_out = False
        for index, (candidate, psm) in enumerate(candidates):
            remaining = deadline - time.monotonic()
            if remaining <= .1:
                timed_out = True
                break
            timeout = remaining * .65 if index == 0 and len(candidates) > 1 else remaining
            try:
                # Explicit variable avoids depending on tessdata/configs/tsv,
                # which is absent from our minimal downloaded language folder.
                result = _run_tesseract([*command, "stdin", "stdout", "-l", "vie+eng", "--oem", "1", "--psm", str(psm), "--dpi", "300", "-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=0"], candidate, timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                continue
            if result.returncode:
                continue
            lines = parse_tsv(result.stdout.decode("utf-8", errors="replace"))
            text = "\n".join(line.text for line in lines).strip()
            if len(text) > 50000:
                raise AppError(422, "OCR_TOO_MUCH_TEXT", "Ảnh chứa quá nhiều nội dung; hãy tải từng trang đơn thuốc")
            if not text:
                continue
            drafts = draft_rows(text, lines)
            def score(item: tuple[str, list[PrescriptionDraft]]) -> tuple[float, int, int]:
                rows = item[1]
                trustworthy_fields = 0.0
                useful_rows = 0
                for row in rows:
                    confidence = row.confidence if row.confidence is not None else 50
                    if confidence < 50:
                        trustworthy_fields -= .5
                        continue
                    uncertain = {review.field for review in row.field_reviews if review.confidence is not None and review.confidence < 80}
                    for administration in row.administrations:
                        fields = {
                            "medication_name": row.medication_name,
                            "instructions": row.instructions,
                            "dose_amount": administration.dose_amount,
                            "dose_unit": administration.dose_unit,
                            "time_of_day": administration.time_of_day,
                            "days_of_week": row.days_of_week,
                        }
                        for field, value in fields.items():
                            if value is not None and value != "" and value != []:
                                trustworthy_fields += confidence / 100 * (.2 if field in uncertain else 1)
                    useful_rows += int(bool(row.instructions) and confidence >= 80)
                # Raw row count must not reward a noisier candidate hallucinating
                # more medicine names. Prefer useful, reliable form data first.
                return round(trustworthy_fields, 3), useful_rows, -len(rows)
            if best is None or score((text, drafts)) > score(best):
                best = (text, drafts)
            if index:
                steps.append("OpenCV: thử ngưỡng sáng thích nghi; OCR bố cục thay thế")
            if drafts and quality_for(drafts).coverage_percent >= 80 and all((row.confidence or 0) >= 85 for row in drafts):
                break
        if best is None:
            if timed_out:
                raise AppError(504, "OCR_TIMEOUT", "OCR mất quá nhiều thời gian. Hãy thử ảnh rõ hơn hoặc nhập thủ công")
            raise AppError(422, "OCR_NO_TEXT", "Không đọc được chữ trong ảnh; hãy chụp rõ toàn bộ đơn thuốc hoặc nhập thủ công")
        text, drafts = best
        quality = quality_for(drafts, round((time.monotonic() - started) * 1000), steps)
        notices = ["Đối chiếu ảnh gốc, đặc biệt tên/hàm lượng và liều ở các ô được đánh dấu, trước khi tạo lịch.", "Tỷ lệ điền sẵn là số ô có dữ liệu, không phải độ chính xác OCR; tên/giờ/ngày có thể cần sửa.", "Chỉ đọc liều ghi rõ trong cách dùng. Hàm lượng và số lượng cấp không được dùng để suy ra liều hay thời gian điều trị."]
        if quality.coverage_percent < 70:
            notices.append("Dữ liệu điền sẵn còn ít. Có thể sửa nhanh hoặc chuyển sang nhập tay để tiết kiệm thời gian.")
        if timed_out:
            notices.append("Đã hết thời gian cho một lượt thử bổ sung; kết quả tốt nhất hiện có được trả về để bạn kiểm tra.")
        return OcrResult(text=text, drafts=drafts, warnings=notices, quality=quality)
    except OSError:
        raise AppError(503, "OCR_UNAVAILABLE", "Bộ đọc chữ không khởi động được. Kiểm tra cài đặt Tesseract hoặc nhập thủ công.") from None
    finally:
        _ocr_slots.release()


def _extract_text(image: bytes) -> str:
    return _extract_document(image).text


async def extract_prescription(image: bytes) -> OcrResult:
    return await run_in_threadpool(_extract_document, image)
