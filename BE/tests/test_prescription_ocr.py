import io
import subprocess
from datetime import date
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import func, select

from app.config import get_settings
from app.errors import AppError
from app.models import AuditLog, MedicationSchedule
from app.services import prescription_ocr as service


def png():
    output = io.BytesIO()
    Image.new("RGB", (30, 30), "white").save(output, "PNG")
    return output.getvalue()


def test_image_validation_and_conservative_drafts():
    assert service.normalize_image(png()).startswith(b"\x89PNG")
    with pytest.raises(AppError):
        service.normalize_image(b"<script>not an image</script>")
    with pytest.raises(AppError):
        service.normalize_image(b"x" * (service.MAX_BYTES + 1))
    text = "BENH VIEN\n1. Thuoc A 500mg\nUống 1 viên sau ăn\n2. Thuoc B 10mg\nSáng 1 viên\nBác sĩ: Nguyen A"
    rows = service.draft_rows(text)
    assert len(rows) == 2
    assert rows[0].medication_name == "Thuoc A 500mg"
    assert rows[0].instructions == "Uống 1 viên sau ăn"
    assert "Bác sĩ" not in rows[1].instructions
    assert "dose_amount" not in rows[0].model_dump()
    assert service.draft_rows("Không rõ thuốc\nUống theo chỉ định") == []
    rows = service.draft_rows("1. A 500mg\nUống 1 viên\n2. B\nUống 2 viên")
    assert rows[0].instructions == "Uống 1 viên"
    assert "Uống 2 viên" not in rows[0].source_text


def test_numbered_hospital_blocks_wrapped_names_quantities_and_topical_use():
    text = """Thuốc điều trị:     Số lượng
1, Thuốc mẫu A (hàm lượng
500mg) X 20 Viên
Uống 1 viên sau ăn
2) Vitamin tổng hợp X 40 Viên
uống ngày 2 lần
3. Thuốc mẫu C (dung dịch 10mg) X 20 Ống
(Hộp 10ml)
uống sáng 1 ống
4 - Thuốc mẫu D 0,1% (15g) X 02 Tuýp
bôi ngoài da trong 7-10
ngày
Lời dặn bác sĩ:
1. Tái khám sau 7 ngày
"""
    drafts = service.draft_rows(text)
    assert len(drafts) == 4
    assert drafts[0].medication_name == "Thuốc mẫu A (hàm lượng 500mg)"
    assert drafts[1].medication_name == "Vitamin tổng hợp"
    assert drafts[2].medication_name.endswith("(Hộp 10ml)")
    assert "X 20" in drafts[0].source_text
    assert drafts[3].instructions == "bôi ngoài da trong 7-10\nngày"
    assert "Tái khám" not in drafts[3].source_text
    assert all("dose_amount" not in draft.model_dump() for draft in drafts)


def test_unrelated_numbered_text_is_not_a_medication():
    assert service.draft_rows("1. Bệnh nhân khám định kỳ\n2. Tái khám sau 10 ngày") == []
    assert service.draft_rows("1. Lời dặn\nUống 500ml nước") == []
    assert service.draft_rows("Bác sĩ khám bệnh\n1. A 500mg") == []


def test_deskew_preserves_blank_image_and_recovers_small_text_tilt():
    assert service.deskew_angle(Image.new("L", (800, 900), 255)) == 0
    page = Image.new("L", (800, 900), 255)
    draw = ImageDraw.Draw(page)
    font = ImageFont.load_default(size=22)
    for index in range(14):
        draw.text((100, 150 + index * 40), f"{index+1}. Sample medicine 500mg - printed text", font=font, fill=0)
    tilted = page.rotate(5, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
    assert abs(service.deskew_angle(tilted) + 5) <= 1


@pytest.mark.parametrize("returncode,output,expected", [
    (1, b"", 422),
    (0, b" \n\x0c", 422),
    (0, b"x" * 50001, 422),
], ids=["process-failed", "blank", "too-long"])
async def test_local_ocr_error_cases(monkeypatch, returncode, output, expected):
    monkeypatch.setattr(service, "check_tesseract", lambda: ["tesseract"])
    monkeypatch.setattr(service, "_run_tesseract", lambda *args: subprocess.CompletedProcess(args, returncode, output, b"private details"))
    with pytest.raises(AppError) as error:
        await service.extract_prescription(png())
    assert error.value.status_code == expected
    assert "private" not in error.value.message


async def test_local_ocr_success_binary_input_and_timeout_cleanup(monkeypatch):
    monkeypatch.setattr(service, "tesseract_command", lambda: ["C:/Program Files/Tesseract/tesseract.exe"])
    def success(arguments, **kwargs):
        assert isinstance(arguments, list)
        assert kwargs.get("shell", False) is False
        assert kwargs["timeout"] > 0
        if "--list-langs" in arguments:
            return subprocess.CompletedProcess(arguments, 0, b"Languages (2):\nvie\neng\n", b"")
        assert arguments[1:3] == ["stdin", "stdout"]
        assert "vie+eng" in arguments
        assert kwargs["input"] == png()
        return subprocess.CompletedProcess(arguments, 0, "1. Thuốc A 500mg\nUống 1 viên sau ăn".encode(), b"")
    monkeypatch.setattr(service.subprocess, "run", success)
    result = await service.extract_prescription(png())
    assert len(result.drafts) == 1
    assert result.provider == "Tesseract OCR (nội bộ)"
    assert result.drafts[0].instructions == "Uống 1 viên sau ăn"
    def timeout(arguments, **kwargs):
        if "--list-langs" in arguments:
            return success(arguments, **kwargs)
        raise subprocess.TimeoutExpired(arguments, 30)
    monkeypatch.setattr(service.subprocess, "run", timeout)
    for _ in range(3):
        with pytest.raises(AppError) as error:
            await service.extract_prescription(png())
        assert error.value.status_code == 504
    monkeypatch.setattr(service.subprocess, "run", success)
    assert len((await service.extract_prescription(png())).drafts) == 1


async def test_busy_ocr_does_not_spawn_a_process(monkeypatch):
    def unexpected():
        pytest.fail("Busy OCR must not launch another process")
    monkeypatch.setattr(service, "check_tesseract", unexpected)
    service._ocr_slots.acquire()
    service._ocr_slots.acquire()
    try:
        with pytest.raises(AppError) as error:
            await service.extract_prescription(png())
        assert error.value.code == "OCR_BUSY"
    finally:
        service._ocr_slots.release()
        service._ocr_slots.release()


def test_missing_language_requires_vietnamese_and_english(monkeypatch):
    monkeypatch.setattr(service, "tesseract_command", lambda: ["tesseract"])
    monkeypatch.setattr(service, "_run_tesseract", lambda args: subprocess.CompletedProcess(args, 0, b"Languages (1):\neng\n", b""))
    with pytest.raises(AppError) as error:
        service.check_tesseract()
    assert error.value.code == "OCR_LANGUAGES_MISSING"


async def test_ocr_access_limits_and_no_automatic_schedule(client, db_factory, monkeypatch):
    from app.routers import prescription_ocr as router
    monkeypatch.setattr(get_settings(), "ocr_requests_per_hour", 1)
    calls = []
    async def fake_extract(data):
        calls.append(data)
        return service.OcrResult(text="1. A 500mg", drafts=service.draft_rows("1. A 500mg"), warnings=["Check"])
    monkeypatch.setattr(router, "extract_prescription", fake_extract)
    account = {"full_name": "Owner", "email": "ocr@example.com", "password": "strong-password"}
    await client.post("/api/v1/auth/register", json=account)
    group = await client.post("/api/v1/care-groups", json={"name": "OCR family"})
    headers = {"X-Care-Group-ID": group.json()["id"], "Content-Type": "image/png"}
    elder = await client.post("/api/v1/elders", headers={"X-Care-Group-ID": group.json()["id"]}, json={"full_name": "Ba"})
    path = f"/api/v1/elders/{elder.json()['id']}/prescription-ocr"
    assert (await client.post(path, headers=headers, content=png())).status_code == 400
    assert (await client.post(path + "?consent=true", headers=headers, content=b"fake")).status_code == 415
    response = await client.post(path + "?consent=true", headers=headers, content=png())
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert len(calls) == 1
    async with db_factory() as db:
        assert await db.scalar(select(func.count()).select_from(MedicationSchedule)) == 0
        audit = await db.scalar(select(AuditLog).where(AuditLog.action == "PRESCRIPTION_OCR_REQUESTED"))
        assert audit.details == {"provider": "tesseract_local"}
    assert (await client.post(path + "?consent=true", headers=headers, content=png())).status_code == 429
    assert len(calls) == 1
    await client.post("/api/v1/auth/logout")
    assert (await client.post(path + "?consent=true", content=png())).status_code == 401
    await client.post("/api/v1/auth/register", json={**account, "email": "other-ocr@example.com"})
    await client.post("/api/v1/care-groups", json={"name": "Other"})
    assert (await client.post(path + "?consent=true", content=png())).status_code == 404
    assert len(calls) == 1


async def test_missing_executable_reports_actionable_error(monkeypatch):
    monkeypatch.setattr(get_settings(), "tesseract_cmd", "Z:/missing/tesseract.exe")
    monkeypatch.setattr(service.shutil, "which", lambda cmd: None)
    with pytest.raises(AppError) as error:
        await service.extract_prescription(png())
    assert error.value.status_code == 503
    assert "TESSERACT_CMD" in error.value.message


async def test_real_ocr_then_explicit_schedule_creation(client, db_factory):
    try:
        service.check_tesseract()
    except AppError:
        pytest.skip("Install Tesseract with vie+eng to run the real OCR integration test")
    sample = Path(__file__).resolve().parents[2] / "docs" / "demo-prescription.png"
    if not sample.exists():
        pytest.skip("Generate the fictional sample with scripts/check_ocr.py")
    account = {"full_name": "OCR integration", "email": "local-ocr@example.com", "password": "integration-password"}
    assert (await client.post("/api/v1/auth/register", json=account)).status_code == 201
    assert (await client.post("/api/v1/care-groups", json={"name": "OCR test"})).status_code == 201
    elder = await client.post("/api/v1/elders", json={"full_name": "Người mẫu"})
    elder_id = elder.json()["id"]
    result = await client.post(
        f"/api/v1/elders/{elder_id}/prescription-ocr?consent=true",
        content=sample.read_bytes(), headers={"Content-Type": "image/png"},
    )
    assert result.status_code == 200, result.text
    assert len(result.json()["drafts"]) == 2
    async with db_factory() as db:
        assert await db.scalar(select(func.count()).select_from(MedicationSchedule)) == 0
    # The user must supply these fields explicitly; the OCR response has none.
    draft = result.json()["drafts"][0]
    assert "dose_amount" not in draft and "time_of_day" not in draft
    saved = await client.post(f"/api/v1/elders/{elder_id}/medication-schedules", json={
        "medication_name": draft["medication_name"], "instructions": draft["instructions"],
        "dose_amount": "1", "dose_unit": "viên", "time_of_day": "08:00:00",
        "start_date": date.today().isoformat(), "days_of_week": [0, 1, 2, 3, 4, 5, 6],
    })
    assert saved.status_code == 201, saved.text
    async with db_factory() as db:
        assert await db.scalar(select(func.count()).select_from(MedicationSchedule)) == 1
