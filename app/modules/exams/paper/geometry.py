"""
paper/geometry.py
==================
OMR Answer Sheet uchun DETERMINISTIK koordinata tizimi (Band 11).

Bu modul faqat sonlarni hisoblaydi — hech qanday chizish/PDF ishi yo'q.
`answer_sheet_pdf.py` shu funksiyalardan foydalanib PDF chizadi, va xuddi
shu funksiyalar keyinchalik (Faza 3) OMR scanner tomonidan --- bir xil
argumentlar bilan chaqirilib --- bubble markazlarini topish uchun ishlatiladi.
Shu tufayli generator va scanner har doim bitta "single source of truth"ga
tayanadi va layout tasodifiy o'zgarib ketmaydi.

Barcha koordinatalar PDF punktlarida (1/72 inch), sahifa pastdan yuqoriga
(reportlab konvensiyasi) hisoblanadi.
"""
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

PAGE_WIDTH, PAGE_HEIGHT = A4

# Har bir savol qatori uchun har doim FIKSIRLANGAN 8 ta variant (A..H) chiziladi
# — hatto savolda 2-3 ta variant bo'lsa ham. Bu shuni ta'minlaydiki, exam
# ichidagi barcha savollar (MULTIPLE_CHOICE, MATCHING, MAP_DIAGRAM va h.k.)
# bir xil grid ustida yotadi va OMR koordinatasi savol turiga qarab
# o'zgarmaydi.
OPTION_LETTERS = ["A", "B", "C", "D", "E", "F", "G", "H"]

MARGIN_TOP = 30 * mm
MARGIN_LEFT = 15 * mm
MARGIN_RIGHT = 15 * mm
MARGIN_BOTTOM = 15 * mm

ROWS_PER_COLUMN = 20
COLUMN_GAP = 8 * mm
ROW_HEIGHT = 9 * mm
BUBBLE_RADIUS = 2.2 * mm
BUBBLE_GAP = 6.2 * mm  # bubble markazlari orasidagi masofa
LABEL_WIDTH = 10 * mm  # "12." raqami uchun joy
TEXT_BOX_WIDTH = 45 * mm  # ochiq (free-text) javob uchun chiziqli joy

QuestionKind = Literal["bubble", "text"]

# Qaysi savol turlari bubble (harfli javob), qaysilari ochiq (yozma) javob
# ekanini belgilaydi (Band 4-6 dagi turlarga mos):
BUBBLE_TYPES = {
    "MULTIPLE_CHOICE",
    "MATCHING",
    "MAP_DIAGRAM",
    "TRUE_FALSE_NOT_GIVEN",
    "HEADINGS_MATCH",
    "TEXT_MATCH",
    "MULTIPLE_SELECT",
}
TEXT_TYPES = {
    "GAP_FILL",
    "GAP_FILL_FILL",
    "SENTENCE_COMPLETION",
    "SHORT_ANSWER",
}


def question_kind(question_type: str) -> QuestionKind:
    return "bubble" if question_type in BUBBLE_TYPES else "text"


@dataclass
class BubblePosition:
    letter: str
    x: float
    y: float


@dataclass
class RowLayout:
    question_number: int
    kind: QuestionKind
    label_x: float
    label_y: float
    bubbles: List[BubblePosition]          # kind == "bubble" bo'lsa to'ldiriladi
    text_box: Optional[Dict[str, float]]   # kind == "text" bo'lsa to'ldiriladi


def usable_columns_per_page() -> int:
    usable_width = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    col_width = LABEL_WIDTH + max(BUBBLE_GAP * len(OPTION_LETTERS), TEXT_BOX_WIDTH) + COLUMN_GAP
    return max(1, int(usable_width // col_width))


def build_layout(total_questions: int) -> Dict[int, RowLayout]:
    """
    1..total_questions bo'lgan har bir savol raqami uchun deterministik
    (faqat total_questions ga bog'liq, savol TURI/MATNIGA bog'liq bo'lmagan
    joylashuvni) qaytaradi. Layout PDF generatordan ham, keyingi OMR
    scannerdan ham bir xil kirish (module+exam_id+version -> total_questions)
    bilan qayta hisoblab olinishi mumkin.
    """
    cols_per_page = usable_columns_per_page()
    col_width = (PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT) / cols_per_page

    layout: Dict[int, RowLayout] = {}

    for idx in range(total_questions):
        q_number = idx + 1
        per_page = ROWS_PER_COLUMN * cols_per_page
        pos_in_page = idx % per_page
        col_in_page = pos_in_page // ROWS_PER_COLUMN
        row_in_col = pos_in_page % ROWS_PER_COLUMN

        col_x0 = MARGIN_LEFT + col_in_page * col_width
        row_y = PAGE_HEIGHT - MARGIN_TOP - row_in_col * ROW_HEIGHT

        label_x = col_x0
        label_y = row_y

        # Bo'sh joyni tekshirmasdan har doim bubble geometriyasini
        # hisoblab qo'yamiz — savol turi PDF chizishda "text" bo'lsa,
        # bubble o'rniga chiziq chiziladi, lekin koordinata hisoblash
        # funksiyasi barcha savollar uchun bir xil ishlaydi.
        bubbles = [
            BubblePosition(
                letter=letter,
                x=col_x0 + LABEL_WIDTH + i * BUBBLE_GAP,
                y=row_y,
            )
            for i, letter in enumerate(OPTION_LETTERS)
        ]
        text_box = {
            "x": col_x0 + LABEL_WIDTH,
            "y": row_y - 1.5 * mm,
            "width": TEXT_BOX_WIDTH,
        }

        layout[q_number] = RowLayout(
            question_number=q_number,
            kind="bubble",  # chaqiruvchi tomon savol turiga qarab kind ni qayta belgilaydi
            label_x=label_x,
            label_y=label_y,
            bubbles=bubbles,
            text_box=text_box,
        )

    return layout


def layout_to_json(layout: Dict[int, RowLayout], kinds: Dict[int, QuestionKind]) -> dict:
    """ExamVersion.answer_sheet_layout ga saqlash uchun JSON-serializable ko'rinish."""
    out = {}
    for q_number, row in layout.items():
        kind = kinds.get(q_number, row.kind)
        out[str(q_number)] = {
            "kind": kind,
            "label": {"x": row.label_x, "y": row.label_y},
            "bubbles": (
                [{"letter": b.letter, "x": b.x, "y": b.y, "r": BUBBLE_RADIUS} for b in row.bubbles]
                if kind == "bubble"
                else []
            ),
            "text_box": row.text_box if kind == "text" else None,
        }
    return out
