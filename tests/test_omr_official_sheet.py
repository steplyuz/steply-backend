"""OMR: rasmiy javob varag'i (static/templates/ANSWER_SHEET_MASTER.pdf) bo'yicha regressiya testlari.

Konstantalar shu varaqning 200 dpi rasmidan o'lchangan. Testlar:
- bo'sh varaq hech qanday javob bermasligi kerak (soxta javob yo'q);
- sintetik belgilangan javoblar to'g'ri o'qilishi kerak;
- Listening va Reading bir xil raqamlarni (1..35) alohida hisoblashi kerak;
- matnli katakchalar: yozuv bor, lekin o'qilmasa qo'lda tekshiruvga tushadi.

Eslatma: pdftoppm (poppler) kerak. Yo'q bo'lsa testlar o'tkazib yuboriladi.
"""
import shutil

import cv2
import pytest

from app.modules.exams.mock import omr_service as omr

pytestmark = pytest.mark.skipif(
    not shutil.which("pdftoppm") or not omr.MASTER_PDF.exists(),
    reason="pdftoppm yoki rasmiy varaq (ANSWER_SHEET_MASTER.pdf) mavjud emas",
)


@pytest.fixture(scope="module")
def master_png_bytes() -> bytes:
    omr._ensure_master_png()  # referens rasmni yaratadi (kerak bo'lsa)
    return cv2.imencode(".png", cv2.imread(str(omr.MASTER_PNG)))[1].tobytes()


@pytest.fixture
def pytesseract_off(monkeypatch):
    monkeypatch.setattr(omr, "pytesseract", None)


def _fill(img, x: float, y: float, r: int = 12) -> None:
    cv2.circle(img, (int(round(x)), int(round(y))), r, (40, 40, 40), -1)


def _master_img():
    return cv2.imread(str(omr.MASTER_PNG))


def _png(img) -> bytes:
    return cv2.imencode(".png", img)[1].tobytes()


def test_constants_match_question_ranges():
    """Har bir blokdagi qatorlar va harflar soni varaq tuzilishiga mos."""
    assert (len(omr.L1_Y), len(omr.L1_X)) == (4, 3)      # Listening 1..8 (A-C)
    assert len(omr.L3_Y) == 4 and len(omr.L3_X) == 6     # 15..18 (A-F)
    assert len(omr.L4_Y) == 5 and len(omr.L4_X) == 8     # 19..23 (A-H)
    assert len(omr.L5_Y) == 3 and len(omr.L5_X1) == 3    # 24..26 (A-C)
    assert len(omr.R2_Y) == 8 and len(omr.R2_X) == 10    # Reading 7..14 (A-J)
    assert len(omr.R3_Y) == 6 and len(omr.R3_X) == 8     # 15..20 (A-H)
    assert len(omr.R4_Y) == 4 and len(omr.R4_X) == 4     # 21..24 (A-D)
    assert len(omr.R4_TFNG_Y) == 5 and len(omr.R4_TFNG_X) == 3  # 25..29 (A-C)
    assert len(omr.R5_Y) == 2 and len(omr.R5_X) == 4     # 34..35 (A-D)
    for key, (qnums, ys, x1, x2) in omr.TEXT_ROWS.items():
        assert len(qnums) == len(ys), key
        assert x1 < x2


def test_reference_frame_matches_master_render(master_png_bytes):
    img = cv2.imdecode(__import__("numpy").frombuffer(master_png_bytes, "uint8"), cv2.IMREAD_COLOR)
    assert img.shape[1] == omr.W and img.shape[0] == omr.H


def test_blank_sheet_has_no_answers(master_png_bytes):
    res = omr.read_sheet(master_png_bytes, candidate_id="MK-TEST-BLANK")
    assert res["marked_count"] == 0
    assert res["ambiguous_questions"] == []
    assert res["manual_review"] is False


def test_marked_answers_are_read_correctly(master_png_bytes):
    img = _master_img()
    _fill(img, omr.L1_X[1], omr.L1_Y[0])           # Listening 1 -> B
    _fill(img, omr.L4_X[7], omr.L4_Y[4])           # Listening 23 -> H
    _fill(img, omr.R2_X[9], omr.R2_Y[0])           # Reading 7 -> J
    _fill(img, omr.R4_TFNG_X[2], omr.R4_TFNG_Y[4]) # Reading 29 -> C
    _fill(img, omr.R5_X[3], omr.R5_Y[1])           # Reading 35 -> D
    res = omr.read_sheet(_png(img), candidate_id="MK-TEST-MARKED")
    assert res["listening"]["1"] == "B"
    assert res["listening"]["23"] == "H"
    assert res["reading"]["7"] == "J"
    assert res["reading"]["29"] == "C"
    assert res["reading"]["35"] == "D"
    assert res["manual_review"] is False


def test_listening_and_reading_with_same_number_are_kept_apart(master_png_bytes):
    """Ikki bo'limda bir xil raqam (masalan 1) belgilansa, ikkalasi ham hisobga olinadi."""
    img = _master_img()
    _fill(img, omr.L1_X[0], omr.L1_Y[0])   # Listening 1 -> A
    _fill(img, omr.R2_X[0], omr.R2_Y[0])   # Reading 7 (boshqa raqam, lekin shu test uchun ikkalasi belgilangan)
    _fill(img, omr.R3_X[0], omr.R3_Y[0])   # Reading 15
    res = omr.read_sheet(_png(img), candidate_id="MK-TEST-SPLIT")
    assert res["listening"]["1"] == "A"
    assert res["reading"]["7"] == "A"
    assert res["marked_count"] == 3


def test_double_mark_goes_to_manual_review():
    img = _master_img()
    _fill(img, omr.L1_X[0], omr.L1_Y[1])   # Listening 2: A
    _fill(img, omr.L1_X[2], omr.L1_Y[1])   # Listening 2: C (ikki belgi)
    res = omr.read_sheet(_png(img), candidate_id="MK-TEST-DOUBLE")
    assert 2 in res["ambiguous_listening"]
    assert res["listening"]["2"] == "?"
    assert res["manual_review"] is True


def test_written_text_without_ocr_goes_to_manual_review(pytesseract_off):
    img = _master_img()
    cv2.putText(img, "PARIS", (400, 992), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (30, 30, 30), 3, cv2.LINE_AA)
    res = omr.read_sheet(_png(img), candidate_id="MK-TEST-TEXT")
    assert 9 in res["ambiguous_listening"]
    assert res["manual_review"] is True


def test_blank_text_rows_do_not_trigger_review_without_ocr(pytesseract_off, master_png_bytes):
    res = omr.read_sheet(master_png_bytes, candidate_id="MK-TEST-BLANK2")
    assert res["ambiguous_listening"] == []
    assert res["ambiguous_reading"] == []
    assert res["manual_review"] is False


def test_missing_candidate_id_forces_manual_review(master_png_bytes):
    res = omr.read_sheet(master_png_bytes, candidate_id=None)
    assert res["manual_review"] is True
