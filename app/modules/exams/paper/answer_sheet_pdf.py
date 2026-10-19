"""
paper/answer_sheet_pdf.py
==========================
OMR Answer Sheet — Band 10, 11.

Layout `geometry.build_layout()` orqali faqat `total_questions` asosida
deterministik hisoblanadi va LOCKED ExamVersion.answer_sheet_layout ga
birinchi generatsiyada "muzlatiladi". Shundan keyin bir xil exam_version
uchun har doim bir xil PDF/koordinata qaytadi — versiya o'zgarmas ekan,
Faza 3 dagi OMR scanner xuddi shu saqlangan layout'dan foydalanadi.
"""
from typing import Dict


from . import geometry


def get_or_freeze_layout(exam_version, number_to_type: Dict[int, str]) -> dict:
    """
    exam_version — paper.models.ExamVersion (locked). Birinchi chaqiriqda
    layout hisoblanib exam_version.answer_sheet_layout ga "muzlatiladi"
    (chaqiruvchi keyin db.flush/commit qiladi). Keyingi barcha chaqiriqlarda
    xuddi shu saqlangan layout qaytadi — versiya o'zgarmas ekan, geometriya
    ham o'zgarmaydi (Band 11).
    """
    if exam_version.answer_sheet_layout:
        return exam_version.answer_sheet_layout

    raw_layout = geometry.build_layout(exam_version.total_questions)
    kinds = {n: geometry.question_kind(t) for n, t in number_to_type.items()}
    layout_json = geometry.layout_to_json(raw_layout, kinds)
    exam_version.answer_sheet_layout = layout_json
    return layout_json


def render_answer_sheet_pdf(
    title: str,
    module_label: str,
    candidate_placeholder: bool,
    layout_json: dict,
) -> bytes:
    """
    Official namuna asosidagi bitta A4 javoblar varaqasi.

    Yuklangan `javoblar_varaqasi_shablon.pdf` aynan OMR/vision grader
    kutayotgan fixed 35 Listening + 35 Reading layoutni beradi. Shuning
    uchun yangi PDF generator bubble koordinatalarini taxmin qilib chizmaydi:
    scanner va bosma varaq bitta master-template'dan foydalanadi.
    """
    from pathlib import Path

    template = Path(__file__).parents[4] / "static" / "templates" / "ANSWER_SHEET_MASTER.pdf"
    if not template.exists():
        template = Path(__file__).with_name("templates_answer_sheet.pdf")
    if not template.exists():
        raise FileNotFoundError("Answer sheet master template topilmadi")
    return template.read_bytes()
