"""Reproducible, offline benchmark using fictional prescriptions only.

Run from the repository root:
    BE\\.venv\\Scripts\\python.exe BE\\scripts\\benchmark_prescription_ocr.py

No network, API key, real patient data, database access, or persistent images.
The six scored fields per expected administration match the form coverage metric:
name, directions, dose, unit, exact clock, and weekdays. Crucially, coverage is
NOT accuracy: this script reports correct, incorrect and omitted fields separately.
Synthetic printed documents cannot establish real hospital-photo accuracy.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
import sys
import time
import unicodedata

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

BE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BE_ROOT))

from app.services.prescription_ocr import extract_prescription, normalize_image  # noqa: E402


@dataclass(frozen=True)
class ExpectedDose:
    amount: float
    unit: str
    clock: str


@dataclass(frozen=True)
class ExpectedMedication:
    name: str
    directions: str
    doses: tuple[ExpectedDose, ...]
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)


@dataclass(frozen=True)
class Case:
    key: str
    layout: str
    degradation: str = "none"


EXPECTED = (
    ExpectedMedication(
        "DemoAlpha 10mg",
        "Uống hằng ngày, sáng 08:00 mỗi lần 1 viên sau ăn.",
        (ExpectedDose(1, "viên", "08:00"),),
    ),
    ExpectedMedication(
        "DemoBeta 20mg",
        "Uống hằng ngày, sáng 07:30 mỗi lần 1 viên; tối 20:00 mỗi lần 2 viên.",
        (ExpectedDose(1, "viên", "07:30"), ExpectedDose(2, "viên", "20:00")),
    ),
    ExpectedMedication(
        "DemoGamma 5ml",
        "Uống hằng ngày, trưa 12:15 mỗi lần 5 ml sau ăn.",
        (ExpectedDose(5, "ml", "12:15"),),
    ),
)

CASES = (
    Case("numbered_clean", "numbered"),
    Case("table_clean", "table"),
    Case("unnumbered_clean", "unnumbered"),
    Case("numbered_perspective", "numbered", "perspective"),
    Case("table_mild_blur_noise", "table", "mild_blur_noise"),
    Case("numbered_low_resolution", "numbered", "low_resolution"),
)


def fold(value: object) -> str:
    """Directions accept accents/punctuation differences, never changed words/numbers."""
    text = unicodedata.normalize("NFD", str(value).casefold().replace("đ", "d"))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", text))


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = (
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / ("arialbd.ttf" if bold else "arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu") / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
    )
    for name in names:
        if name.is_file():
            return ImageFont.truetype(str(name), size)
    raise RuntimeError("Install Arial or DejaVu Sans; Vietnamese-capable fonts are required.")


def draw_wrapped(draw: ImageDraw.ImageDraw, value: str, x: int, y: int, width: int, face: ImageFont.FreeTypeFont) -> int:
    words = value.split()
    line = ""
    for word in words:
        attempt = f"{line} {word}".strip()
        if line and draw.textlength(attempt, font=face) > width:
            draw.text((x, y), line, fill="black", font=face)
            y += 55
            line = word
        else:
            line = attempt
    if line:
        draw.text((x, y), line, fill="black", font=face)
        y += 55
    return y


def render_case(case: Case) -> bytes:
    page = Image.new("RGB", (1800, 1500), "white")
    draw = ImageDraw.Draw(page)
    title, face, strong = font(48, True), font(36), font(38, True)
    draw.text((80, 60), "ĐƠN THUỐC GIẢ LẬP - CHỈ KIỂM THỬ OCR", fill="black", font=title)
    draw.text((80, 145), "Không có bệnh nhân thật. Không dùng để điều trị.", fill="black", font=face)
    draw.text((80, 225), "Thuốc điều trị", fill="black", font=strong)
    y = 310
    for index, medicine in enumerate(EXPECTED, 1):
        if case.layout == "table":
            # Wide visual columns test spatial row regrouping. Quantity is NOT a dose.
            draw.text((85, y), f"{index}. {medicine.name}", fill="black", font=strong)
            draw.text((1440, y), f"X {20 + index * 10}", fill="black", font=face)
            y = draw_wrapped(draw, medicine.directions, 110, y + 62, 1450, face) + 60
        elif case.layout == "unnumbered":
            draw.text((85, y), medicine.name, fill="black", font=strong)
            y = draw_wrapped(draw, medicine.directions, 85, y + 62, 1620, face) + 65
        else:
            draw.text((85, y), f"{index}. {medicine.name}", fill="black", font=strong)
            y = draw_wrapped(draw, medicine.directions, 115, y + 62, 1580, face) + 60
    draw.text((85, y + 50), "Lời dặn: Đối chiếu thông tin trước khi lưu lịch.", fill="black", font=face)
    raster = np.asarray(page)
    if case.degradation == "perspective":
        source = np.float32([[0, 0], [1799, 0], [1799, 1499], [0, 1499]])
        target = np.float32([[165, 95], [1805, 180], [1870, 1635], [90, 1570]])
        raster = cv2.warpPerspective(raster, cv2.getPerspectiveTransform(source, target), (1980, 1740), borderValue=(55, 55, 55))
    elif case.degradation == "mild_blur_noise":
        raster = cv2.GaussianBlur(raster, (5, 5), .8)
        noise = np.random.default_rng(20261004).normal(0, 3.5, raster.shape)
        raster = np.clip(raster.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    elif case.degradation == "low_resolution":
        # Deliberately weak input: accurately withheld values should count as omissions.
        raster = cv2.resize(raster, (450, 375), interpolation=cv2.INTER_AREA)
        raster = cv2.GaussianBlur(raster, (3, 3), .9)
    encoded = cv2.imencode(".png", cv2.cvtColor(raster, cv2.COLOR_RGB2BGR))[1].tobytes()
    return normalize_image(encoded)


def absent(value: object) -> bool:
    return value is None or value == "" or value == []


def equal(field: str, actual: object, expected: object) -> bool:
    if field in {"name", "directions", "unit"}:
        return fold(actual) == fold(expected)
    if field == "dose":
        return isinstance(actual, (int, float)) and abs(float(actual) - float(expected)) < .0001
    if field == "time":
        return str(actual)[:5] == expected
    return tuple(sorted(actual)) == tuple(sorted(expected))


def evaluate(result: object) -> dict:
    drafts = result.drafts
    available = set(range(len(drafts)))
    correct = incorrect = omitted = extra_slots = 0
    details = []
    for medicine in EXPECTED:
        best = max(available, key=lambda index: SequenceMatcher(None, fold(medicine.name), fold(drafts[index].medication_name)).ratio(), default=None)
        if best is not None and SequenceMatcher(None, fold(medicine.name), fold(drafts[best].medication_name)).ratio() < .35:
            best = None
        actual = drafts[best] if best is not None else None
        if best is not None:
            available.remove(best)
        slots = actual.administrations if actual else []
        extra_slots += max(0, len(slots) - len(medicine.doses))
        for index, dose in enumerate(medicine.doses):
            slot = slots[index] if index < len(slots) else None
            expected_fields = {"name": medicine.name, "directions": medicine.directions, "dose": dose.amount, "unit": dose.unit, "time": dose.clock, "weekdays": medicine.weekdays}
            actual_fields = {"name": actual.medication_name if actual else None, "directions": actual.instructions if actual else None, "dose": slot.dose_amount if slot else None, "unit": slot.dose_unit if slot else None, "time": slot.time_of_day if slot else None, "weekdays": actual.days_of_week if actual else None}
            for field, expected in expected_fields.items():
                value = actual_fields[field]
                state = "omitted" if absent(value) else "correct" if equal(field, value, expected) else "incorrect"
                correct += state == "correct"
                incorrect += state == "incorrect"
                omitted += state == "omitted"
                if state != "correct":
                    details.append({"medicine": medicine.name, "administration": index + 1, "field": field, "state": state, "expected": expected, "actual": value})
    total = correct + incorrect + omitted
    return {
        "expected_fields": total,
        "correct_filled": correct,
        "incorrect_filled": incorrect,
        "omitted": omitted,
        "correct_filled_percent": round(correct * 100 / total, 1),
        "filled_expected_percent": round((correct + incorrect) * 100 / total, 1),
        "backend_fill_percent": result.quality.coverage_percent,
        "extra_medications": len(available),
        "extra_administrations": extra_slots,
        "differences": details,
    }


async def run(args: argparse.Namespace) -> list[dict]:
    reports = []
    for case in CASES:
        if args.case and case.key not in args.case:
            continue
        started = time.perf_counter()
        try:
            image = render_case(case)
            if args.save_fixtures:
                args.save_fixtures.mkdir(parents=True, exist_ok=True)
                (args.save_fixtures / f"{case.key}.png").write_bytes(image)
            result = await extract_prescription(image)
            report = {"case": case.key, "layout": case.layout, "degradation": case.degradation, **evaluate(result), "elapsed_ms": round((time.perf_counter() - started) * 1000), "ocr_elapsed_ms": result.quality.elapsed_ms, "preprocessing": result.quality.preprocessing, "ocr_text": result.text, "drafts": [draft.model_dump() for draft in result.drafts]}
        except Exception as error:
            total = 6 * sum(len(medicine.doses) for medicine in EXPECTED)
            report = {"case": case.key, "layout": case.layout, "degradation": case.degradation, "expected_fields": total, "correct_filled": 0, "incorrect_filled": 0, "omitted": total, "correct_filled_percent": 0, "filled_expected_percent": 0, "backend_fill_percent": 0, "elapsed_ms": round((time.perf_counter() - started) * 1000), "error": f"{type(error).__name__}: {error}"}
        reports.append(report)
        if not args.json:
            print(f"{case.key:30} correct={report['correct_filled']:2}/{report['expected_fields']} ({report['correct_filled_percent']:5.1f}%)  incorrect={report['incorrect_filled']:2} omitted={report['omitted']:2}  fill={report['backend_fill_percent']:3}%  {report['elapsed_ms']:5}ms")
            if report.get("error"):
                print(f"  ERROR {report['error']}")
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=[case.key for case in CASES], help="Select case(s); default runs all.")
    parser.add_argument("--json", action="store_true", help="Print complete reports, OCR text and structured drafts as JSON.")
    parser.add_argument("--output", type=Path, help="Optionally save benchmark JSON, containing only generated fictional data.")
    parser.add_argument("--save-fixtures", type=Path, help="Optionally export generated PNGs for UI testing; every image is clearly marked fictional.")
    args = parser.parse_args()
    if args.output:
        args.output = args.output.resolve()
    if args.save_fixtures:
        args.save_fixtures = args.save_fixtures.resolve()
    # BE config loads .env from cwd; work identically from repository root or BE.
    os.chdir(BE_ROOT)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    reports = asyncio.run(run(args))
    artifact = {"fixture_notice": "Synthetic fictional prescriptions; no clinical accuracy claim. No real patient data.", "metric": "Six fields per expected administration; names/directions/units ignore diacritics/punctuation only; words and numbers must match. Missing drafts remain in denominator. Extra drafts/admins reported separately. Fill coverage is not correctness.", "reports": reports}
    if args.output:
        args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(artifact, ensure_ascii=False, indent=2))
    else:
        print("Synthetic printed samples only. Coverage != correctness; never treat these results as accuracy on real hospital photos.")
    return 1 if any(report.get("error") for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
