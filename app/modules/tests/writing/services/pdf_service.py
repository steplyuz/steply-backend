from app.core.paths import STATIC_DIR
import asyncio
import html
import io
import json
import os
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.clock import utcnow

# Steply brand colors
BRAND_DARK = colors.HexColor("#151515")
BRAND_ACCENT = colors.HexColor("#E4F70A")
BRAND_ACCENT_DARK = colors.HexColor("#B5C900")
BRAND_CREAM = colors.HexColor("#FBFAF6")
TEXT_MAIN = colors.HexColor("#151515")
TEXT_MUTED = colors.HexColor("#5b5b55")
LINE = colors.HexColor("#e4e2d8")

NATIONAL_LABELS = {
    "vocabulary": "Leksika",
    "coherence": "Bog‘lanish va matn yaxlitligi",
    "task_fulfilment": "Topshiriq talablarining bajarilishi",
    "grammar": "Grammatika",
}
LEGACY_LABELS = {
    "task_achievement": "Topshiriqning bajarilishi",
    "coherence": "Bog‘lanish va matn yaxlitligi",
    "vocabulary": "Leksika",
    "grammar": "Grammatika",
    "mechanics": "Imlo va punktuatsiya",
    "overall": "Umumiy",
}
V3_LABELS = {
    "task_completion": "Topshiriqning bajarilishi",
    "grammar": "Grammatika",
    "vocabulary": "Leksika",
    "coherence": "Bog‘lanish va matn yaxlitligi",
    "mechanics": "Imlo va punktuatsiya",
}
STATUS_LABELS = {
    "HUMAN_FINAL": "Yakuniy — o‘qituvchi tomonidan tasdiqlangan",
    "AI_FINAL": "Yakuniy — AI tomonidan baholangan",
    "TEACHER_PENDING": "AI bahosi amalda; o‘qituvchi tekshiruvi kutilmoqda",
    "AI_DRAFT": "Qisman AI bahosi — barcha topshiriqlar baholanmagan",
    "PENDING": "Tekshiruv kutilmoqda",
    "AI_PENDING": "Javob saqlandi — AI krediti/limiti kutilmoqda",
    "AI_FAILED": "AI baholashda xato — qayta urinib ko‘ring",
}



class _FeedbackSelection(str):
    """String-friendly feedback result that remains unpackable as (text, payload)."""
    def __new__(cls, rendered: str, text=None, payload=None):
        obj = str.__new__(cls, rendered)
        obj.text = text
        obj.payload = payload
        return obj

    def __iter__(self):
        yield self.text
        yield self.payload

def _fmt_num(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


class PDFService:
    def __init__(self):
        self.logo_path = str(STATIC_DIR / "logo.png")
        self.organization_name = "STEPLY"
        self.watermark_text = "STEPLY"
        self.font_regular, self.font_bold = self._register_unicode_fonts()

    @staticmethod
    def _register_unicode_fonts() -> tuple[str, str]:
        """Unicode shriftlar (o‘, g‘, kirill). Har so‘rovda PDFService qayta yaratiladi,
        shuning uchun allaqachon ro‘yxatdan o‘tgan bold shrift yo‘qolib qolmasligi kerak."""
        candidates = [
            (STATIC_DIR / "fonts" / "DejaVuSans.ttf", STATIC_DIR / "fonts" / "DejaVuSans-Bold.ttf"),
            (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
            (Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"), Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf")),
            (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
        ]
        for regular, bold in candidates:
            if not regular.is_file():
                continue
            regular_name, bold_name = "SteplyWritingUnicode", "SteplyWritingUnicodeBold"
            registered = pdfmetrics.getRegisteredFontNames()
            if regular_name not in registered:
                pdfmetrics.registerFont(TTFont(regular_name, str(regular)))
            if bold.is_file():
                if bold_name not in registered:
                    pdfmetrics.registerFont(TTFont(bold_name, str(bold)))
            else:
                bold_name = regular_name
            # <b> teglari Unicode shrift bilan ishlashi uchun oila ro‘yxatdan o‘tkaziladi.
            registerFontFamily(
                regular_name,
                normal=regular_name,
                bold=bold_name,
                italic=regular_name,
                boldItalic=bold_name,
            )
            return regular_name, bold_name
        return "Helvetica", "Helvetica-Bold"

    # -------------------------------------------------------
    # HEADER + FOOTER + WATERMARK
    # -------------------------------------------------------
    def _draw_header_footer(self, canvas_obj, doc):
        canvas_obj.saveState()
        width, height = A4

        # Watermark
        canvas_obj.saveState()
        canvas_obj.setFillColorRGB(0.95, 0.95, 0.93)
        canvas_obj.setFont("Helvetica-Bold", 72)
        canvas_obj.translate(width / 2, height / 2)
        canvas_obj.rotate(35)
        canvas_obj.drawCentredString(0, 0, self.watermark_text)
        canvas_obj.restoreState()

        # Header strip + accent line
        canvas_obj.setFillColor(BRAND_DARK)
        canvas_obj.rect(0, height - 22 * mm, width, 22 * mm, stroke=0, fill=1)
        canvas_obj.setFillColor(BRAND_ACCENT)
        canvas_obj.rect(0, height - 23.2 * mm, width, 1.2 * mm, stroke=0, fill=1)

        # Logo yoki wordmark
        drew_logo = False
        if os.path.exists(self.logo_path):
            try:
                canvas_obj.drawImage(
                    ImageReader(self.logo_path),
                    15 * mm, height - 18 * mm,
                    width=22 * mm, height=12 * mm,
                    preserveAspectRatio=True, mask="auto",
                )
                drew_logo = True
            except Exception:
                drew_logo = False
        if not drew_logo:
            canvas_obj.setFont("Helvetica-Bold", 16)
            canvas_obj.setFillColor(BRAND_ACCENT)
            canvas_obj.drawString(15 * mm, height - 14.5 * mm, self.organization_name)

        canvas_obj.setFont("Helvetica-Bold", 10)
        canvas_obj.setFillColor(colors.white)
        canvas_obj.drawRightString(width - 15 * mm, height - 14 * mm, "WRITING NATIJA HISOBOTI")

        # Footer
        canvas_obj.setStrokeColor(LINE)
        canvas_obj.setLineWidth(0.8)
        canvas_obj.line(15 * mm, 16 * mm, width - 15 * mm, 16 * mm)
        canvas_obj.setFont(self.font_regular, 9)
        canvas_obj.setFillColor(TEXT_MUTED)
        canvas_obj.drawString(15 * mm, 8.5 * mm, "Steply • Rasmiy hisobot")
        canvas_obj.drawRightString(width - 15 * mm, 8.5 * mm, f"{doc.page}-bet")
        canvas_obj.restoreState()

    # -------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------
    @staticmethod
    def _safe_paragraph(text, style):
        safe = html.escape(str(text) if text is not None else "").replace("\n", "<br/>")
        return Paragraph(safe, style)

    def _fmt_dt(self, dt) -> str:
        if not dt:
            return utcnow().strftime("%Y-%m-%d %H:%M")
        try:
            if isinstance(dt, str):
                return dt.replace("T", " ")[:16]
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(dt)

    def _get_user_name(self, result) -> str:
        user = getattr(result, "user", None)
        profile = getattr(user, "profile", None) if user else None
        full_name = getattr(profile, "full_name", None) if profile else None
        username = getattr(profile, "username", None) if profile else None
        return (full_name or username or "Nomzod").strip()

    def _get_exam_title(self, result) -> str:
        exam = getattr(result, "exam", None)
        return str(
            getattr(exam, "title", None) or getattr(result, "exam_id", None) or "Writing imtihoni"
        ).strip()

    @staticmethod
    def _enum_value(value):
        return getattr(value, "value", value)

    def _task_title(self, ans) -> str:
        task = getattr(ans, "task", None)
        part = getattr(task, "part_number", None) if task else None
        sub = getattr(task, "sub_part", None) if task else None
        if part is not None and sub is not None:
            return f"{part}.{sub}-topshiriq"
        if part is not None:
            return f"{part}-topshiriq"
        return "Topshiriq"

    @staticmethod
    def _task_sort_key(ans):
        task = getattr(ans, "task", None)
        return (
            getattr(task, "part_number", 0) or 0,
            getattr(task, "sub_part", 0) or 0,
            getattr(ans, "task_id", 0) or 0,
        )

    def _prompt_block_from_task(self, ans) -> str:
        task = getattr(ans, "task", None)
        if not task:
            return "Topshiriq matni topilmadi."
        parts = []
        for label, attr in (("Mavzu", "topic"), ("Kontekst", "context_text"), ("Ko‘rsatma", "instruction"), ("Savol", "question")):
            value = (getattr(task, attr, None) or "").strip()
            if value:
                parts.append(f"{label}:\n{value}")
        return "\n\n".join(parts) if parts else "Topshiriq matni topilmadi."

    def _pick_feedback(self, ans):
        """Tanlangan izohni matn ko‘rinishida qaytaradi; legacy unpacking ham saqlanadi."""
        feedbacks = getattr(ans, "feedbacks", None) or []
        if not feedbacks:
            return None, None

        def _source(item) -> str:
            return str(self._enum_value(getattr(item, "source", None)))

        fb = (
            next((i for i in reversed(feedbacks) if _source(i) == "HUMAN"), None)
            or next((i for i in reversed(feedbacks) if _source(i) == "AI"), feedbacks[-1])
        )
        content = (getattr(fb, "content", None) or "").strip()
        if not content:
            return None, None
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return _FeedbackSelection(content, content, None)
        if not isinstance(payload, dict):
            return _FeedbackSelection(content, content, None)

        lines = [str(payload.get("summary") or payload.get("feedback") or "").strip()]
        rubric = payload.get("rubric_results") or {}
        band = rubric.get("english_band") if isinstance(rubric, dict) else None
        if isinstance(band, dict) and band.get("band_score") is not None:
            lines.append(f"English band: {_fmt_num(band['band_score'])}/9")
        for error in (payload.get("errors") or []):
            if isinstance(error, dict):
                correction = error.get("correction")
                if correction:
                    lines.append(str(correction))
        corrected = payload.get("corrected_text")
        if corrected:
            lines.append(str(corrected))
        rendered = "\n".join(line for line in lines if line)
        return _FeedbackSelection(rendered, None, payload)

    # -------------------------------------------------------
    # BLOCK BUILDERS
    # -------------------------------------------------------
    def _kv_table(self, rows, styles):
        data = [
            [Paragraph(f"<b>{html.escape(k)}</b>", styles["label"]), self._safe_paragraph(v, styles["normal"])]
            for k, v in rows
        ]
        table = Table(data, colWidths=[48 * mm, 117 * mm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BRAND_CREAM),
            ("GRID", (0, 0), (-1, -1), 0.5, LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return table

    def _score_table(self, header, rows, styles, footer=None):
        data = [[Paragraph(f"<b>{html.escape(header[0])}</b>", styles["label"]),
                 Paragraph(f"<b>{html.escape(header[1])}</b>", styles["label"])]]
        for name, value in rows:
            data.append([self._safe_paragraph(name, styles["normal"]), self._safe_paragraph(value, styles["normal"])])
        if footer:
            data.append([Paragraph(f"<b>{html.escape(footer[0])}</b>", styles["normal"]),
                         Paragraph(f"<b>{html.escape(footer[1])}</b>", styles["normal"])])
        table = Table(data, colWidths=[120 * mm, 30 * mm], repeatRows=1)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_ACCENT),
            ("GRID", (0, 0), (-1, -1), 0.5, LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]
        if footer:
            style.append(("BACKGROUND", (0, -1), (-1, -1), BRAND_CREAM))
        table.setStyle(TableStyle(style))
        return table

    def _criteria_block(self, ans, payload, styles):
        """Faqat haqiqatan baholangan mezonlar ko‘rsatiladi (yo‘q mezon 0 deb chiqmaydi)."""
        elements = []
        if payload and payload.get("version") == 3:
            line = f"Topshiriq bali: <b>{_fmt_num(payload.get('score', 0))} / {_fmt_num(payload.get('max_score', 0))}</b>"
            elements += [Paragraph(line, styles["normal"])]
            if payload.get("length_cap_applied"):
                elements.append(Paragraph(
                    f"Hajm talabi bajarilmagani uchun ball {_fmt_num(payload.get('model_score'))} dan pasaytirildi.",
                    styles["label"]))
            elements.append(Spacer(1, 4 * mm))
            return elements
        national = ((payload or {}).get("rubric_results") or {}).get("national")
        if isinstance(national, dict) and isinstance(national.get("scores"), dict) and national["scores"]:
            rows = [
                (NATIONAL_LABELS.get(key, key.replace("_", " ").capitalize()), f"{_fmt_num(val)} / 4")
                for key, val in national["scores"].items()
            ]
            total = national.get("total_raw_16")
            if total is None:
                try:
                    total = sum(float(v) for v in national["scores"].values())
                except (TypeError, ValueError):
                    total = None
            footer = ("Jami", f"{_fmt_num(total)} / 16") if total is not None else None
            elements += [Paragraph("<b>Baholash mezonlari</b>", styles["label"]), Spacer(1, 1.5 * mm),
                         self._score_table(("Mezon", "Ball"), rows, styles, footer), Spacer(1, 4 * mm)]
        else:
            score_map = {}
            for s in getattr(ans, "scores", None) or []:
                key = self._enum_value(getattr(s, "criterion", None))
                if key:
                    score_map[str(key)] = getattr(s, "score", None)
            rows = [
                (LEGACY_LABELS.get(key, key), _fmt_num(val))
                for key, val in score_map.items() if val is not None
            ]
            if rows:
                elements += [Paragraph("<b>Baholash mezonlari</b>", styles["label"]), Spacer(1, 1.5 * mm),
                             self._score_table(("Mezon", "Ball"), rows, styles), Spacer(1, 4 * mm)]

        band = ((payload or {}).get("rubric_results") or {}).get("english_band")
        if isinstance(band, dict) and band.get("band_score") is not None:
            elements.append(Paragraph(f"English band: <b>{_fmt_num(band['band_score'])} / 9</b>", styles["normal"]))
            elements.append(Spacer(1, 3 * mm))
        return elements

    def _feedback_block(self, ans, styles):
        text, payload = self._pick_feedback(ans)
        elements = []
        if text is None and payload is None:
            elements += [Paragraph("<b>Izoh</b>", styles["label"]), Paragraph("Izoh berilmagan.", styles["normal"])]
            return elements, None

        if payload is not None:
            elements += self._criteria_block(ans, payload, styles)

        elements.append(Paragraph("<b>Umumiy tahlil</b>", styles["label"]))
        summary = text if payload is None else (payload.get("summary") or payload.get("feedback") or "Izoh berilmagan.")
        elements.append(self._safe_paragraph(summary, styles["normal"]))

        if payload is not None:
            v3 = payload.get("criteria_comments") if payload.get("version") == 3 else None
            if isinstance(v3, dict) and v3:
                elements += [Spacer(1, 3 * mm), Paragraph("<b>Mezonlar bo‘yicha izohlar</b>", styles["label"])]
                for crit, comment in v3.items():
                    elements.append(self._safe_paragraph(f"{V3_LABELS.get(crit, crit)}: {comment}", styles["normal"]))
            rubric = payload.get("rubric_results") or {}
            for key in ("national", "english_band"):
                comments = (rubric.get(key) or {}).get("comments") if isinstance(rubric.get(key), dict) else None
                if isinstance(comments, str) and comments.strip():
                    comments = {"izoh": comments}
                if isinstance(comments, dict) and comments:
                    elements.append(Spacer(1, 3 * mm))
                    title = "Mezonlar bo‘yicha izohlar" if key == "national" else "English band izohlari"
                    elements.append(Paragraph(f"<b>{title}</b>", styles["label"]))
                    for crit, comment in comments.items():
                        label = "Xulosa" if crit in ("summary", "izoh") else NATIONAL_LABELS.get(crit, LEGACY_LABELS.get(crit, str(crit).replace("_", " ").capitalize()))
                        elements.append(self._safe_paragraph(f"{label}: {comment}", styles["normal"]))

            errors = [e for e in (payload.get("errors") or []) if isinstance(e, dict)][:25]
            if errors:
                elements += [Spacer(1, 4 * mm), Paragraph("<b>Aniqlangan xatolar va tuzatishlar</b>", styles["label"]), Spacer(1, 1.5 * mm)]
                data = [[Paragraph(f"<b>{h}</b>", styles["label"]) for h in ("Asl matn", "Tuzatish", "Izoh")]]
                for e in errors:
                    data.append([
                        self._safe_paragraph(e.get("original", "—"), styles["small"]),
                        self._safe_paragraph(e.get("correction", "—"), styles["small"]),
                        self._safe_paragraph(e.get("explanation", "—"), styles["small"]),
                    ])
                tbl = Table(data, colWidths=[50 * mm, 50 * mm, 65 * mm], repeatRows=1)
                tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), BRAND_ACCENT),
                    ("GRID", (0, 0), (-1, -1), 0.5, LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                elements.append(tbl)

            corrected = (payload.get("corrected_text") or "").strip()
            if corrected:
                elements += [Spacer(1, 4 * mm), Paragraph("<b>Yaxshilangan variant</b>", styles["label"]),
                             self._safe_paragraph(corrected, styles["normal"])]
        return elements, payload

    # -------------------------------------------------------
    # MAIN
    # -------------------------------------------------------
    async def generate_pdf_report(self, result):
        # ReportLab sinxron va CPU talab qiladi: event loop'ni bloklamaslik uchun thread'da.
        return await asyncio.to_thread(self._generate_pdf_report_sync, result)

    def _generate_pdf_report_sync(self, result):
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4,
            rightMargin=15 * mm, leftMargin=15 * mm,
            topMargin=30 * mm, bottomMargin=22 * mm,
            title="Steply — Writing natija hisoboti", author="Steply",
        )
        base = getSampleStyleSheet()
        styles = {
            "title": ParagraphStyle("StTitle", parent=base["Heading1"], fontName=self.font_bold,
                                    fontSize=18, alignment=1, spaceAfter=8, textColor=TEXT_MAIN),
            "section": ParagraphStyle("StSection", parent=base["Heading2"], fontName=self.font_bold,
                                      fontSize=13, textColor=TEXT_MAIN, spaceBefore=6, spaceAfter=6),
            "label": ParagraphStyle("StLabel", parent=base["Normal"], fontName=self.font_regular,
                                    fontSize=9.5, leading=12, textColor=TEXT_MUTED),
            "normal": ParagraphStyle("StNormal", parent=base["Normal"], fontName=self.font_regular,
                                     fontSize=10.5, leading=14, textColor=TEXT_MAIN),
            "small": ParagraphStyle("StSmall", parent=base["Normal"], fontName=self.font_regular,
                                    fontSize=9, leading=11.5, textColor=TEXT_MAIN),
        }

        result_id = getattr(result, "id", None)
        report_id = f"STEPLY-W-{int(result_id):06d}" if isinstance(result_id, int) else "STEPLY-W"
        status_code = getattr(result, "evaluation_status", None)
        is_finalized = bool(getattr(result, "is_finalized", False))
        raw_score = getattr(result, "raw_score", None)
        scaled_score = getattr(result, "scaled_score", None)

        elements = [Paragraph("WRITING NATIJA HISOBOTI", styles["title"]),
                    HRFlowable(width="100%", thickness=1.0, color=LINE), Spacer(1, 6 * mm)]

        rows = []
        if result_id is not None:
            rows.append(("Natija ID", str(result_id)))
        rows += [
            ("Nomzod", self._get_user_name(result)),
            ("Imtihon", self._get_exam_title(result)),
            ("Topshirilgan sana", self._fmt_dt(getattr(result, "created_at", None))),
            ("Hisobot ID", report_id),
        ]
        # CEFR darajasi faqat barcha topshiriqlar baholangan yakuniy natijada ko‘rsatiladi.
        cefr = self._enum_value(getattr(result, "cefr_level", None))
        if is_finalized and cefr:
            rows.append(("CEFR darajasi", str(cefr)))
        if scaled_score is not None:
            rows.append(("Yakuniy ball", f"{_fmt_num(scaled_score)} / 75"))
        if raw_score is not None:
            rows.append(("Xom ball (topshiriqlar yig‘indisi)", f"{_fmt_num(raw_score)} / 16"))
        rows.append(("Holati", STATUS_LABELS.get(status_code, "Yakuniy" if is_finalized else "Tekshiruv kutilmoqda")))
        elements += [self._kv_table(rows, styles), PageBreak()]

        answers = list(getattr(result, "answers", None) or [])
        try:
            answers.sort(key=self._task_sort_key)
        except Exception:
            pass

        if not answers:
            elements += [Paragraph("Javoblar topilmadi", styles["section"]), Spacer(1, 4 * mm),
                         Paragraph("Natijani qayta yuklab ko‘ring yoki administratorga murojaat qiling.", styles["normal"])]
        else:
            for idx, ans in enumerate(answers, start=1):
                elements += [
                    Paragraph(self._task_title(ans), styles["section"]),
                    Paragraph("<b>Topshiriq sharti</b>", styles["label"]),
                    self._safe_paragraph(self._prompt_block_from_task(ans), styles["normal"]),
                    Spacer(1, 4 * mm),
                    Paragraph("<b>Nomzod javobi</b>", styles["label"]),
                    self._safe_paragraph(getattr(ans, "content", "") or "(javob kiritilmagan)", styles["normal"]),
                    Spacer(1, 4 * mm),
                ]
                meta = []
                if getattr(ans, "word_count", None) is not None:
                    meta.append(f"So‘zlar soni: {ans.word_count}")
                pen = getattr(ans, "penalty", None)
                if pen:
                    meta.append(f"Hajm uchun jarima: −{_fmt_num(pen)}")
                if getattr(ans, "raw_score", None) is not None:
                    meta.append(f"Topshiriq bali: {_fmt_num(ans.raw_score)}")
                if meta:
                    elements += [Paragraph(" • ".join(meta), styles["label"]), Spacer(1, 4 * mm)]

                fb_elements, _payload = self._feedback_block(ans, styles)
                elements += fb_elements
                if idx != len(answers):
                    elements.append(PageBreak())

        doc.build(elements, onFirstPage=self._draw_header_footer, onLaterPages=self._draw_header_footer)
        return buffer.getvalue()
