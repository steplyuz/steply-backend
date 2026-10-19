"""Question-paper PDF generation for Reading and Listening.

If the supplied official master PDF is installed, it remains the immutable page
background and content is rendered into its fixed slots. Source archives may
not include the licensed/approved master files; in that case we generate a
clearly labelled, readable fallback paper from the same exam content instead
of making the PDF endpoint fail with HTTP 503.
"""
from __future__ import annotations

import html
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, KeepTogether

from .template_paper_pdf import (
    READING_MASTER,
    LISTENING_MASTER,
    WRITING_MASTER,
    render_reading_template,
    render_listening_template,
    render_writing_template,
)


class PdfNotReadyError(Exception):
    """Exam hali lock qilinmagan yoki savollar to'liq emas."""

PAPER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PAPER_DIR.parents[3]
REFERENCE_DIR = PROJECT_ROOT / "static" / "templates"
REFERENCE_TEMPLATES = {
    "LISTENING": LISTENING_MASTER,
    "READING": READING_MASTER,
}


def _unicode_font_names() -> tuple[str, str]:
    """Choose a Unicode font where available (Linux server or Windows host)."""
    candidates = [
        (PROJECT_ROOT / "static" / "fonts" / "DejaVuSans.ttf", PROJECT_ROOT / "static" / "fonts" / "DejaVuSans-Bold.ttf"),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
        (Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"), Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf")),
        (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
    ]
    for regular, bold in candidates:
        if regular.is_file():
            regular_name = "SteplyFallbackUnicode"
            bold_name = "SteplyFallbackUnicodeBold"
            if regular_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(regular_name, str(regular)))
            if bold.is_file() and bold_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(bold_name, str(bold)))
            else:
                bold_name = regular_name
            return regular_name, bold_name
    return "Helvetica", "Helvetica-Bold"


def _safe_text(value) -> str:
    """Escape user-authored content for ReportLab Paragraph XML."""
    return html.escape(str(value or "")).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")


def _fallback_paper(exam: dict, skill: str) -> bytes:
    regular, bold = _unicode_font_names()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "SteplyTitle", parent=styles["Title"], fontName=bold, fontSize=18,
        leading=23, alignment=TA_CENTER, textColor=colors.HexColor("#111827"), spaceAfter=5 * mm,
    )
    subtitle_style = ParagraphStyle(
        "SteplySubtitle", parent=styles["Normal"], fontName=regular, fontSize=9,
        leading=12, alignment=TA_CENTER, textColor=colors.HexColor("#4B5563"), spaceAfter=4 * mm,
    )
    h_style = ParagraphStyle(
        "SteplyPart", parent=styles["Heading2"], fontName=bold, fontSize=13,
        leading=17, textColor=colors.HexColor("#111827"), spaceBefore=4 * mm, spaceAfter=2 * mm,
        keepWithNext=True,
    )
    body_style = ParagraphStyle(
        "SteplyBody", parent=styles["BodyText"], fontName=regular, fontSize=9.5,
        leading=13, textColor=colors.HexColor("#1F2937"), spaceAfter=2.5 * mm,
    )
    small_style = ParagraphStyle(
        "SteplySmall", parent=body_style, fontSize=8.5, leading=11,
        textColor=colors.HexColor("#4B5563"),
    )
    question_style = ParagraphStyle(
        "SteplyQuestion", parent=body_style, fontName=bold, spaceBefore=2 * mm,
        keepWithNext=True,
    )
    buffer = BytesIO()

    def draw_footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D1D5DB"))
        canvas.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
        canvas.setFont(regular, 7.5)
        canvas.setFillColor(colors.HexColor("#6B7280"))
        canvas.drawString(18 * mm, 9 * mm, "Steply · generated fallback paper")
        canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"{doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=17 * mm, bottomMargin=19 * mm,
        title=f"{skill.title()} — {exam.get('title') or 'Practice test'}",
        author="Steply",
    )
    story = [
        Paragraph(_safe_text(exam.get("title") or f"{skill.title()} test"), title_style),
        Paragraph(
            _safe_text(f"{skill} · {exam.get('cefr_level') or ''} · {exam.get('duration_minutes') or ''} daqiqa"),
            subtitle_style,
        ),
        Paragraph(
            "Eslatma: rasmiy master PDF shabloni mavjud emas yoki matn unga sig‘madi; hujjat test ma’lumotlaridan muqobil tartibda tuzildi.",
            small_style,
        ),
        Spacer(1, 3 * mm),
    ]

    for part_index, part in enumerate(exam.get("parts") or [], start=1):
        questions = sorted(
            part.get("questions") or [],
            key=lambda item: int(item.get("question_number") or 0),
        )
        # Some imported tests duplicate the same shared answer bank on every
        # question. Hoist an identical bank to the part level so the PDF prints
        # it once, while preserving per-question options when they differ.
        shared_question_options = None
        if not part.get("options") and questions:
            option_sets = [q.get("options") or [] for q in questions]
            if option_sets[0] and all(options == option_sets[0] for options in option_sets[1:]):
                shared_question_options = option_sets[0]
        part_title = part.get("title") or f"Part {part.get('part_number') or part_index}"
        story.append(Paragraph(_safe_text(f"Part {part.get('part_number') or part_index}: {part_title}"), h_style))
        for field, label in (("instruction", "Ko‘rsatma"), ("description", "Ko‘rsatma"), ("context", "Kontekst")):
            if part.get(field):
                story.append(Paragraph(f"<b>{label}:</b> {_safe_text(part[field])}", small_style))
        if part.get("passage"):
            story.append(Paragraph("<b>Matn</b>", small_style))
            story.append(Paragraph(_safe_text(part["passage"]), body_style))
        shared_options = part.get("options") or shared_question_options or []
        if shared_options:
            story.append(Paragraph("<b>Umumiy javob variantlari</b>", small_style))
            for option in shared_options:
                if isinstance(option, dict):
                    label = option.get("label") or option.get("value") or ""
                    value = option.get("value") or option.get("label") or ""
                    text = f"{label}. {value}" if label != value else str(value)
                else:
                    text = str(option)
                story.append(Paragraph("• " + _safe_text(text), small_style))

        for question in questions:
            number = question.get("question_number") or "—"
            text = question.get("text") or question.get("question") or ""
            block = [Paragraph(f"{_safe_text(number)}. {_safe_text(text)}", question_style)]
            options = [] if shared_question_options is not None and not part.get("options") else (question.get("options") or [])
            for option in options:
                if isinstance(option, dict):
                    label = option.get("label") or ""
                    value = option.get("value") or ""
                    option_text = f"{label}. {value}" if label else value
                else:
                    option_text = str(option)
                block.append(Paragraph("• " + _safe_text(option_text), small_style))
            if "GAP_FILL" in str(question.get("type") or "").upper():
                block.append(Paragraph("Javob: __________________________________________", small_style))
            else:
                block.append(Spacer(1, 2 * mm))
            story.append(KeepTogether(block))
        story.append(Spacer(1, 3 * mm))

    tasks = sorted(exam.get("tasks") or [], key=lambda item: (int(item.get("part_number") or 99), int(item.get("sub_part") or 0), int(item.get("id") or 0)))
    for task in tasks:
        part_number = task.get("part_number") or "—"
        sub_part = task.get("sub_part")
        task_title = f"Task {part_number}.{sub_part}" if sub_part else f"Task {part_number}"
        story.append(Paragraph(_safe_text(task_title), h_style))
        for field, label in (("context_text", "Kontekst"), ("instruction", "Ko‘rsatma"), ("topic", "Topshiriq")):
            if task.get(field):
                story.append(Paragraph(f"<b>{label}:</b> {_safe_text(task[field])}", body_style))
        fmt = task.get("format") or {}
        min_words, max_words = fmt.get("min_words"), fmt.get("max_words")
        if min_words and max_words:
            story.append(Paragraph(f"So‘zlar soni: {min_words}–{max_words}", small_style))
        elif min_words:
            story.append(Paragraph(f"Kamida {min_words} so‘z yozing.", small_style))
        story.append(Spacer(1, 4 * mm))

    if not (exam.get("parts") or tasks):
        story.append(Paragraph("Ushbu testda PDFga chiqariladigan topshiriqlar mavjud emas.", body_style))

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buffer.getvalue()


def render_reading_pdf(exam: dict) -> bytes:
    """Use official master if it fits; otherwise generate a readable fallback."""
    if READING_MASTER.is_file():
        try:
            return render_reading_template(exam)
        except (FileNotFoundError, ValueError):
            # A content-heavy paper must remain downloadable even if a fixed
            # official slot cannot fit it; the fallback explicitly labels itself.
            pass
    return _fallback_paper(exam, "READING")


def render_listening_pdf(exam: dict) -> bytes:
    """Use official master if it fits; otherwise generate a readable fallback."""
    if LISTENING_MASTER.is_file():
        try:
            return render_listening_template(exam)
        except (FileNotFoundError, ValueError):
            pass
    return _fallback_paper(exam, "LISTENING")


def render_writing_pdf(exam: dict) -> bytes:
    """Use official Writing master if it fits; otherwise generate a readable fallback."""
    if WRITING_MASTER.is_file():
        try:
            return render_writing_template(exam)
        except (FileNotFoundError, ValueError):
            pass
    return _fallback_paper(exam, "WRITING")
