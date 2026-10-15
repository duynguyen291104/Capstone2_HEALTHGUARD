"""Regression cases for different hospital layouts and unsafe dose assumptions."""

import io
import subprocess

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.config import get_settings
from app.errors import AppError
from app.services import prescription_ocr as service


def one(text: str):
    rows = service.draft_rows("Thuốc điều trị\n1. Thuốc mẫu 500mg X 20 viên\n" + text)
    assert len(rows) == 1
    return rows[0]


def test_explicit_dose_clock_frequency_and_duration_prefill():
    draft = one("Uống 1 viên ngày 2 lần, sáng 08:00, tối 20:30; trong 7 ngày")
    assert (draft.dose_amount, draft.dose_unit) == (1, "viên")
    assert [(item.label, item.time_of_day, item.dose_amount) for item in draft.administrations] == [("Sáng", "08:00", 1), ("Tối", "20:30", 1)]
    assert draft.days_of_week == list(range(7))
    assert draft.duration_days == 7
    assert service.quality_for([draft]).coverage_percent == 100


def test_different_doses_per_administration_never_become_one_shared_dose():
    draft = one("Uống sáng 1 viên, tối 2 viên")
    assert draft.dose_amount is None and draft.dose_unit is None
    assert [(item.label, item.dose_amount, item.dose_unit, item.time_of_day) for item in draft.administrations] == [("Sáng", 1, "viên", None), ("Tối", 2, "viên", None)]
    assert {review.field for review in draft.field_reviews} >= {"dose_amount", "time_of_day", "days_of_week"}
    before = one("Uống 1 viên sáng, 2 viên tối")
    assert [(item.label, item.dose_amount) for item in before.administrations] == [("Sáng", 1), ("Tối", 2)]


@pytest.mark.parametrize("instruction", [
    "Uống ngày 2 lần", "Uống 1-2 viên khi đau", "Uống 1 đến 2 viên khi đau", "Uống 2 viên chia 2 lần",
    "Bôi mỏng ngày 2 lần", "Uống theo chỉ định bác sĩ",
])
def test_ambiguous_missing_or_daily_total_dose_is_unset(instruction):
    draft = one(instruction)
    assert draft.dose_amount is None
    assert all(item.dose_amount is None for item in draft.administrations)
    assert any(item.field == "dose_amount" for item in draft.field_reviews)


def test_prn_alternating_days_and_duration_ranges_do_not_create_daily_assumptions():
    for instruction in ("Uống 1 viên khi cần ngày 2 lần", "Uống 1 viên cách ngày", "Uống 1 viên khi đau"):
        draft = one(instruction)
        assert draft.days_of_week is None
        assert any(review.field == "days_of_week" for review in draft.field_reviews)
    draft = one("Bôi mỏng ngày 2 lần sáng chiều, trong 7-10 ngày")
    assert draft.duration_days is None
    assert any(review.field == "duration_days" for review in draft.field_reviews)
    assert draft.dose_amount is None


def test_strength_dispensed_quantity_and_pack_size_never_become_dose():
    for name in ("Thuốc mẫu 500mg X20 viên", "Dung dịch 10mg (Hộp 10ml) X 20 ống"):
        rows = service.draft_rows("Thuốc điều trị\n1. " + name)
        assert len(rows) == 1
        assert rows[0].dose_amount is None and rows[0].duration_days is None
        assert "X20" not in rows[0].medication_name and "X 20" not in rows[0].medication_name


def test_route_words_in_medication_name_do_not_turn_pack_size_into_dose():
    for name in ("Tobradex (dung dịch nhỏ mắt) 5ml", "Thuốc nhỏ mắt Tobradex 5ml", "Thuốc tiêm Insulin 100UI/ml", "Thuốc uống mẫu 500mg"):
        draft = service.draft_rows("Thuốc điều trị\n1. " + name)[0]
        assert draft.medication_name == name
        assert draft.instructions == "" and draft.dose_amount is None
    inline = service.draft_rows("1. Thuốc mẫu 500mg Uống 1 viên mỗi ngày")[0]
    assert inline.medication_name == "Thuốc mẫu 500mg" and inline.dose_amount == 1


def test_unnumbered_table_and_wrapped_names_produce_separate_drafts():
    text = """ĐƠN THUỐC
Tên thuốc | Số lượng | Cách dùng
Thuốc mẫu A 500mg | 20 viên | Uống 1 viên ngày 2 lần
Thuốc mẫu B 10mg | 10 viên | Uống tối 1 viên
Thuốc mẫu C (hàm lượng
10mg) X 10 ống
Uống sáng 1 ống
Bác sĩ Nguyễn A
"""
    drafts = service.draft_rows(text)
    assert [row.medication_name for row in drafts] == ["Thuốc mẫu A 500mg", "Thuốc mẫu B 10mg", "Thuốc mẫu C (hàm lượng 10mg)"]
    assert [row.dose_amount for row in drafts] == [1, 1, 1]
    assert drafts[1].administrations[0].label == "Tối"


def test_patient_diagnosis_and_date_headers_do_not_hide_medication_section():
    text = "Bệnh nhân: Nguyễn A\nChẩn đoán: Viêm họng\nNgày 4 tháng 10 năm 2026\nThuốc điều trị\nThuốc mẫu A 500mg\nUống 1 viên mỗi ngày"
    drafts = service.draft_rows(text)
    assert len(drafts) == 1 and drafts[0].medication_name == "Thuốc mẫu A 500mg"


def test_fraction_decimal_weekdays_and_explicit_hours():
    for source, amount in (("Uống 1/2 viên thứ 2, thứ 4, thứ 6 vào 08:00", .5), ("Uống 2,5 ml mỗi ngày vào 09h30", 2.5)):
        draft = one(source)
        assert draft.dose_amount == amount
        assert draft.administrations[0].time_of_day in {"08:00", "09:30"}
    assert one("Uống 1/2 viên thứ 2, thứ 4, thứ 6 vào 08:00").days_of_week == [0, 2, 4]


def test_maximum_frequency_is_not_the_evening_and_wrapped_daily_usage_is_kept():
    draft = one("Uống 1 viên khi đau, tối đa 3 lần/ngày")
    assert draft.days_of_week is None
    assert all(item.label != "Tối" for item in draft.administrations)
    draft = one("Uống sáng 1 viên\nHằng ngày trong 7 ngày")
    assert draft.days_of_week == list(range(7)) and draft.duration_days == 7
    for hour in ("8h", "08h", "8 giờ"):
        assert one("Uống 1 viên mỗi ngày vào " + hour).administrations[0].time_of_day == "08:00"


def tsv(rows):
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    return header + "\n" + "\n".join(f"5\t1\t{block}\t1\t{line}\t{index}\t{x}\t{y}\t{len(word)*9}\t18\t{confidence}\t{word}" for index, (word, confidence, x, y, block, line) in enumerate(rows, 1))


def test_tsv_boxes_reunite_columns_and_keep_weak_numeric_confidence():
    output = tsv([
        ("1.", 95, 10, 100, 1, 1), ("Thuốc", 95, 40, 100, 1, 1), ("A", 95, 100, 100, 1, 1), ("500mg", 95, 125, 100, 1, 1),
        ("X", 95, 500, 100, 2, 1), ("20", 95, 530, 100, 2, 1), ("viên", 95, 570, 100, 2, 1),
        ("Uống", 95, 40, 140, 1, 2), ("8", 22, 100, 140, 1, 2), ("viên", 95, 120, 140, 1, 2), ("sáng", 95, 180, 140, 1, 2),
    ])
    lines = service.parse_tsv(output)
    assert len(lines) == 2
    assert lines[0].text == "1. Thuốc A 500mg X 20 viên"
    assert lines[1].words[1].confidence == 22
    draft = service.draft_rows("\n".join(line.text for line in lines), lines)[0]
    assert draft.confidence > 85  # Line average must not conceal a weak digit.
    assert draft.dose_amount is None
    assert all(item.dose_amount is None for item in draft.administrations)
    review = next(item for item in draft.field_reviews if item.field == "dose_amount" and item.confidence == 22)
    assert "Con số" in review.reason


@pytest.mark.parametrize("bad_token,expected_field", [
    ("g", "dose_unit"), ("08:00", "time_of_day"), ("7", "duration_days"), ("2", "administrations"),
])
def test_low_word_confidence_withholds_only_the_corresponding_field(bad_token, expected_field):
    rows = []
    for index, token in enumerate("1. Thuốc A 500mg".split()):
        rows.append((token, 96, index * 90, 100, 1, 1))
    for index, token in enumerate("Uống 1 g ngày 2 lần vào 08:00 trong 7 ngày".split()):
        rows.append((token, 25 if token == bad_token else 96, index * 70, 150, 1, 2))
    lines = service.parse_tsv(tsv(rows))
    draft = service.draft_rows("", lines)[0]
    assert any(review.field == expected_field and review.confidence == 25 for review in draft.field_reviews)
    if bad_token == "g":
        assert draft.dose_amount is None and draft.dose_unit is None
    else:
        assert draft.dose_amount == 1 and draft.dose_unit == "g"
    if bad_token == "08:00":
        assert all(item.time_of_day is None for item in draft.administrations)
    if bad_token == "7":
        assert draft.duration_days is None
    if bad_token == "2":
        assert draft.days_of_week is None


def test_image_metadata_orientation_and_bounded_candidates_stay_in_memory():
    page = Image.new("RGB", (100, 200), "white")
    ImageDraw.Draw(page).text((5, 20), "1. Sample 500mg", fill="black")
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private patient metadata"
    data = io.BytesIO()
    page.save(data, "JPEG", exif=exif)
    normalized = service.normalize_image(data.getvalue())
    with Image.open(io.BytesIO(normalized)) as checked:
        assert checked.size == (200, 100)
        assert not checked.getexif()
        assert "private" not in str(checked.info)
    candidates, steps = service.prepare_ocr_images(normalized)
    assert len(candidates) == 2
    assert any("OpenCV" in step for step in steps)
    for encoded, psm in candidates:
        with Image.open(io.BytesIO(encoded)) as checked:
            assert max(checked.size) <= 3200
            assert not checked.getexif()
        assert psm in {3, 6}


def test_rectification_requires_large_bright_paper_boundary():
    blank = np.full((700, 900), 255, np.uint8)
    output, rectified = service.rectify_document(blank)
    assert rectified is False and output.shape == blank.shape
    photo = np.full((900, 900), 35, np.uint8)
    cv2.fillConvexPoly(photo, np.array([[120, 90], [800, 150], [720, 800], [90, 760]], np.int32), 245)
    _, rectified = service.rectify_document(photo)
    assert rectified is True
    small = np.full((900, 900), 35, np.uint8)
    cv2.rectangle(small, (300, 300), (500, 500), 255, -1)
    assert service.rectify_document(small)[1] is False


def test_all_ocr_attempts_share_total_deadline_and_semaphore_releases(monkeypatch):
    monkeypatch.setattr(get_settings(), "ocr_timeout_seconds", 10)
    clock = [0.0]
    monkeypatch.setattr(service.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(service, "check_tesseract", lambda **kwargs: ["tesseract"])
    monkeypatch.setattr(service, "prepare_ocr_images", lambda data: ([(b"variant1", 3), (b"variant2", 6)], []))
    budgets = []
    def timeout(arguments, image=None, timeout=None):
        budgets.append(timeout)
        clock[0] += timeout
        raise subprocess.TimeoutExpired(arguments, timeout)
    monkeypatch.setattr(service, "_run_tesseract", timeout)
    with pytest.raises(AppError) as error:
        service._extract_document(b"image")
    assert error.value.code == "OCR_TIMEOUT"
    assert len(budgets) == 2 and sum(budgets) == pytest.approx(10)
    assert service._ocr_slots.acquire(blocking=False)
    service._ocr_slots.release()


def test_fallback_returns_best_usable_result_when_second_attempt_times_out(monkeypatch):
    monkeypatch.setattr(service, "check_tesseract", lambda **kwargs: ["tesseract"])
    monkeypatch.setattr(service, "prepare_ocr_images", lambda data: ([(b"variant1", 3), (b"variant2", 6)], []))
    def process(arguments, image=None, timeout=None):
        if image == b"variant2":
            raise subprocess.TimeoutExpired(arguments, timeout)
        return subprocess.CompletedProcess(arguments, 0, "1. A 500mg\nUống 1 viên".encode(), b"private stderr")
    monkeypatch.setattr(service, "_run_tesseract", process)
    result = service._extract_document(b"image")
    assert len(result.drafts) == 1
    assert any("lượt thử bổ sung" in warning for warning in result.warnings)
    assert all("private" not in warning for warning in result.warnings)


def test_candidate_selection_prefers_trustworthy_fields_over_more_garbage_rows(monkeypatch):
    monkeypatch.setattr(service, "check_tesseract", lambda **kwargs: ["tesseract"])
    monkeypatch.setattr(service, "prepare_ocr_images", lambda data: ([(b"clear", 3), (b"noisy", 6)], []))
    clear = tsv([
        ("1.", 96, 10, 100, 1, 1), ("A", 96, 40, 100, 1, 1), ("500mg", 96, 70, 100, 1, 1),
        ("Uống", 96, 10, 140, 1, 2), ("1", 96, 70, 140, 1, 2), ("viên", 96, 95, 140, 1, 2),
    ])
    noisy = tsv([
        ("1.", 30, 10, 100, 1, 1), ("Garbled", 30, 40, 100, 1, 1), ("500mg", 30, 140, 100, 1, 1),
        ("2.", 30, 10, 160, 1, 2), ("Garbage", 30, 40, 160, 1, 2), ("10mg", 30, 140, 160, 1, 2),
    ])
    monkeypatch.setattr(service, "_run_tesseract", lambda args, image=None, **kwargs: subprocess.CompletedProcess(args, 0, (clear if image == b"clear" else noisy).encode(), b""))
    result = service._extract_document(b"image")
    assert len(result.drafts) == 1 and result.drafts[0].medication_name == "A 500mg"


def test_quality_counts_filled_fields_not_claimed_reading_accuracy():
    draft = one("Uống sáng 1 viên")
    quality = service.quality_for([draft])
    assert quality.prefilled_fields == 4 and quality.total_fields == 6
    assert quality.coverage_percent == 67
    assert quality.low_confidence_fields >= 2
    assert service.OcrResult(text="", drafts=[], warnings=[]).quality.coverage_percent == 0
