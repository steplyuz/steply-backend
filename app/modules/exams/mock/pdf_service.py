from __future__ import annotations

from app.core.paths import STATIC_DIR
import html
import os
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import ExamSession, MockRegistration, MockExamResult, MockExam

# Region enum qiymatlarini (masalan "tashkent_city") o'zbekcha o'qiladigan
# nomga aylantirish uchun lug'at. Kod qiymati topilmasa, xom qiymat qaytariladi.
REGION_LABELS_UZ = {
    "tashkent_city": "Toshkent shahri",
    "tashkent_region": "Toshkent viloyati",
    "andijan": "Andijon",
    "bukhara": "Buxoro",
    "fergana": "Farg'ona",
    "jizzakh": "Jizzax",
    "kashkadarya": "Qashqadaryo",
    "navoiy": "Navoiy",
    "namangan": "Namangan",
    "samarkand": "Samarqand",
    "surkhandarya": "Surxondaryo",
    "syrdarya": "Sirdaryo",
    "khorezm": "Xorazm",
    "karakalpakstan": "Qoraqalpog'iston",
}

# Davomat varag'i jadvalidagi tor "Hududi" ustuni uchun qisqartirilgan shakl
# (bitta qatorga sig'ishi uchun) — boshqa hujjatlarda to'liq nom ishlatiladi.
REGION_LABELS_SHORT_UZ = {
    **REGION_LABELS_UZ,
    "tashkent_city": "Toshkent sh.",
    "tashkent_region": "Toshkent vil.",
}


def _region_label(region: str) -> str:
    if not region:
        return ""
    key = region.value if hasattr(region, "value") else region
    return REGION_LABELS_UZ.get(key, key)


def _region_label_short(region: str) -> str:
    if not region:
        return ""
    key = region.value if hasattr(region, "value") else region
    return REGION_LABELS_SHORT_UZ.get(key, key)


class _BasePDFService:
    """ReferralPDFService va ResultPDFService uchun umumiy header/footer."""

    def __init__(self):
        self.logo_path = str(STATIC_DIR / "logo.png")
        self.organization_name = "ENWIS EDUCATION CENTER"
        self.font_regular, self.font_bold = self._register_unicode_fonts()

    @staticmethod
    def _register_unicode_fonts() -> tuple[str, str]:
        candidates = [
            (STATIC_DIR / "fonts" / "DejaVuSans.ttf", STATIC_DIR / "fonts" / "DejaVuSans-Bold.ttf"),
            (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
            (Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"), Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf")),
            (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
        ]
        for regular, bold in candidates:
            if not regular.is_file():
                continue
            regular_name, bold_name = "SteplyMockUnicode", "SteplyMockUnicodeBold"
            if regular_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(regular_name, str(regular)))
            if bold.is_file() and bold_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(bold_name, str(bold)))
            else:
                bold_name = regular_name
            return regular_name, bold_name
        return "Helvetica", "Helvetica-Bold"

    def _header_footer(self, canvas_obj, doc, subtitle: str = ""):
        canvas_obj.saveState()
        width, height = A4

        canvas_obj.setFillColor(colors.HexColor("#0f172a"))
        canvas_obj.rect(0, height - 22 * mm, width, 22 * mm, stroke=0, fill=1)

        if os.path.exists(self.logo_path):
            logo = ImageReader(self.logo_path)
            canvas_obj.drawImage(
                logo, 15 * mm, height - 18 * mm, width=22 * mm, height=12 * mm,
                preserveAspectRatio=True, mask="auto",
            )

        canvas_obj.setFillColor(colors.white)
        canvas_obj.setFont("Helvetica-Bold", 13)
        canvas_obj.drawString(42 * mm, height - 13 * mm, self.organization_name)
        canvas_obj.setFont("Helvetica", 9)
        canvas_obj.drawString(42 * mm, height - 18 * mm, subtitle)

        canvas_obj.setFillColor(colors.HexColor("#94a3b8"))
        canvas_obj.setFont("Helvetica", 8)
        canvas_obj.drawCentredString(width / 2, 10 * mm, "Ushbu hujjat elektron shaklda yaratilgan")

        canvas_obj.restoreState()


class ReferralPDFService(_BasePDFService):
    """
    Mock imtihonga 'yo'llama' (referral) hujjatini yaratadi:
    F.I.Sh, test sanasi, manzil, hudud va kerakli hujjatlar ro'yxati.
    """

    def _header_footer(self, canvas_obj, doc):
        super()._header_footer(canvas_obj, doc, subtitle="Mock imtihonga yo'llama")

    # --- Davomat varag'i uchun YENGIL (bannersiz) sahifa dekoratsiyasi ---
    # Katta rangli banner o'rniga faqat yupqa chiziq + sahifa raqami: bo'sh
    # joy tejaladi, 30 kishigacha bitta A4 sahifaga sig'adi.
    def _roster_page_decor(self, canvas_obj, doc):
        canvas_obj.saveState()
        width, height = A4

        canvas_obj.setStrokeColor(colors.HexColor("#0f172a"))
        canvas_obj.setLineWidth(1)
        canvas_obj.line(10 * mm, height - 12 * mm, width - 10 * mm, height - 12 * mm)

        canvas_obj.setFillColor(colors.HexColor("#0f172a"))
        canvas_obj.setFont("Helvetica-Bold", 9)
        canvas_obj.drawString(10 * mm, height - 9 * mm, self.organization_name)

        canvas_obj.setFillColor(colors.HexColor("#94a3b8"))
        canvas_obj.setFont("Helvetica", 7.5)
        canvas_obj.drawRightString(
            width - 10 * mm, height - 9 * mm,
            "Ushbu hujjat elektron shaklda yaratilgan",
        )

        canvas_obj.setFillColor(colors.HexColor("#94a3b8"))
        canvas_obj.setFont("Helvetica", 8)
        canvas_obj.drawCentredString(width / 2, 8 * mm, f"{doc.page}-sahifa")

        canvas_obj.restoreState()

    async def generate_roster(
        self,
        session: ExamSession,
        exam_title: str,
        registrations: list[MockRegistration],
    ) -> bytes:
        """
        Bosib chiqarish uchun davomat varag'i — oxirida bo'sh 'Imzo' ustuni bilan.
        Katta banner-header YO'Q (joy tejash uchun); ~30 kishi bitta A4
        sahifaga sig'adigan zich, ammo o'qilishi oson dizayn.
        """
        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            topMargin=16 * mm, bottomMargin=13 * mm,
            leftMargin=10 * mm, rightMargin=10 * mm,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "Title", parent=styles["Title"], fontName=self.font_bold, fontSize=13.5, leading=16,
            spaceAfter=2, textColor=colors.HexColor("#0f172a"),
        )
        meta_style = ParagraphStyle(
            "Meta", parent=styles["Normal"], fontName=self.font_regular, fontSize=9, leading=12,
            textColor=colors.HexColor("#475569"),
        )

        story = [
            Paragraph("Mock imtihon — davomat varag'i", title_style),
            Paragraph(
                f"<b>{exam_title}</b>  &nbsp;|&nbsp;  "
                f"{session.exam_date.strftime('%d.%m.%Y %H:%M')}  &nbsp;|&nbsp;  "
                f"{session.location_address}  &nbsp;|&nbsp;  "
                f"Jami: <b>{len(registrations)}</b> nafar",
                meta_style,
            ),
            Spacer(1, 3 * mm),
        ]

        header_style = ParagraphStyle(
            "TableHeader", parent=styles["Normal"], fontSize=8.5,
            textColor=colors.white, fontName=self.font_bold, leading=10,
        )
        header = [
            Paragraph(h, header_style)
            for h in ["#", "F.I.Sh.", "Student ID", "Tug'ilgan sana", "Jinsi", "Hududi", "Test kuni", "Imzo"]
        ]
        cell_style = ParagraphStyle("Cell", parent=styles["Normal"], fontName=self.font_regular, fontSize=8.5, leading=10)
        mono_cell_style = ParagraphStyle("MonoCell", parent=styles["Normal"], fontName=self.font_regular, fontSize=7.8, leading=9)
        exam_day_str = session.exam_date.strftime("%d.%m.%Y")
        rows = [header]
        for i, r in enumerate(registrations, start=1):
            rows.append([
                str(i),
                Paragraph(html.escape(r.full_name or ""), cell_style),
                Paragraph(html.escape(r.student_id or "-"), mono_cell_style),
                r.date_of_birth.strftime("%d.%m.%Y") if r.date_of_birth else "",
                "Erkak" if r.gender == "male" else "Ayol",
                Paragraph(html.escape(_region_label_short(r.region)), cell_style),
                exam_day_str,
                "",  # qo'lda imzo qo'yish uchun bo'sh joy
            ])

        table = Table(
            rows,
            colWidths=[8 * mm, 48 * mm, 30 * mm, 19 * mm, 11 * mm, 23 * mm, 17 * mm, 34 * mm],
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, -1), self.font_regular),
                    ("FONTNAME", (0, 0), (-1, 0), self.font_bold),
                    ("ALIGN", (0, 0), (0, -1), "CENTER"),
                    ("ALIGN", (3, 0), (3, -1), "CENTER"),
                    ("ALIGN", (4, 0), (4, -1), "CENTER"),
                    ("ALIGN", (6, 0), (6, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#0f172a")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                    ("TOPPADDING", (0, 0), (-1, 0), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
                    ("TOPPADDING", (0, 1), (-1, -1), 2.5),
                    ("BOTTOMPADDING", (0, 1), (-1, -1), 2.5),
                    # 30 kishi ~1 betga sig'ishi uchun qat'iy, ammo imzoga
                    # yetarli minimal balandlik.
                    ("MINIMUMHEIGHT", (0, 1), (-1, -1), 7.6 * mm),
                ]
            )
        )
        story.append(table)

        story.append(Spacer(1, 6 * mm))
        sign_style = ParagraphStyle("Sign", parent=styles["Normal"], fontName=self.font_regular, fontSize=9, leading=12)
        story.append(Paragraph(
            "Nazoratchi (F.I.Sh.): _______________________________  &nbsp;&nbsp;&nbsp; "
            "Imzo: ______________",
            sign_style,
        ))

        doc.build(
            story,
            onFirstPage=self._roster_page_decor,
            onLaterPages=self._roster_page_decor,
        )
        return buf.getvalue()

    async def generate_referral(
        self,
        registration: MockRegistration,
        session: ExamSession,
        exam_title: str,
    ) -> bytes:
        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            topMargin=32 * mm, bottomMargin=18 * mm,
            leftMargin=18 * mm, rightMargin=18 * mm,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "Title", parent=styles["Title"], fontName=self.font_bold, fontSize=16, spaceAfter=6,
        )
        normal = ParagraphStyle("NormalUz", parent=styles["Normal"], fontName=self.font_regular, fontSize=11, leading=16)
        label = ParagraphStyle("Label", parent=styles["Normal"], fontName=self.font_regular, fontSize=10, textColor=colors.HexColor("#64748b"))

        story = []
        story.append(Paragraph("Mock imtihonga yo'llama", title_style))
        story.append(Paragraph(html.escape(exam_title or ""), normal))
        story.append(Spacer(1, 10 * mm))

        exam_date_str = session.exam_date.strftime("%d.%m.%Y  %H:%M")
        required_docs = [
            d.strip() for d in (session.required_documents or "").split(";") if d.strip()
        ]

        info_rows = [
            ["F.I.Sh.", registration.full_name],
            ["CEFR/Mock kod", registration.student_id or "-"],
            ["Tug'ilgan sana", registration.date_of_birth.strftime("%d.%m.%Y") if registration.date_of_birth else "-"],
            ["Yoshi", str(registration.age)],
            ["Jinsi", "Erkak" if registration.gender == "male" else "Ayol"],
            ["Yashash hududi", _region_label(registration.region)],
            ["Test sanasi", exam_date_str],
            ["Manzil", session.location_address],
        ]

        table = Table(info_rows, colWidths=[45 * mm, 115 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 10.5),
                    ("FONTNAME", (0, 0), (-1, -1), self.font_regular),
                    ("FONTNAME", (0, 0), (0, -1), self.font_bold),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#64748b")),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 10 * mm))

        story.append(Paragraph("Kerakli hujjatlar:", label))
        story.append(Spacer(1, 2 * mm))
        for doc_item in required_docs:
            story.append(Paragraph(f"•  {html.escape(doc_item)}", normal))

        story.append(Spacer(1, 12 * mm))
        story.append(Paragraph(
            "Iltimos, imtihon boshlanishidan 30 daqiqa oldin keling va yuqoridagi "
            "hujjatlarni o'zingiz bilan olib keling.",
            normal,
        ))

        doc.build(
            story,
            onFirstPage=self._header_footer,
            onLaterPages=self._header_footer,
        )
        return buf.getvalue()


class ResultPDFService(_BasePDFService):
    """Mock imtihon (Reading/Listening/Writing/Speaking) yakuniy natijasi PDF hisoboti."""

    def _header_footer(self, canvas_obj, doc):
        super()._header_footer(canvas_obj, doc, subtitle="Mock imtihon natijasi")

    async def generate_result(
        self,
        result: MockExamResult,
        exam: MockExam,
        user_full_name: str,
    ) -> bytes:
        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            topMargin=32 * mm, bottomMargin=18 * mm,
            leftMargin=18 * mm, rightMargin=18 * mm,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle("Title", parent=styles["Title"], fontName=self.font_bold, fontSize=16, spaceAfter=6)
        normal = ParagraphStyle("NormalUz", parent=styles["Normal"], fontName=self.font_regular, fontSize=11, leading=16)
        label = ParagraphStyle("Label", parent=styles["Normal"], fontName=self.font_regular, fontSize=10, textColor=colors.HexColor("#64748b"))

        story = [
            Paragraph("Mock imtihon natijasi", title_style),
            Paragraph(html.escape(exam.title or ""), normal),
            Spacer(1, 8 * mm),
        ]

        info_rows = [
            ["F.I.Sh.", user_full_name],
            ["Imtihon", exam.title],
            ["Sana", result.created_at.strftime("%d.%m.%Y %H:%M")],
        ]
        info_table = Table(info_rows, colWidths=[40 * mm, 120 * mm])
        info_table.setStyle(
            TableStyle([
                ("FONTSIZE", (0, 0), (-1, -1), 10.5),
                ("FONTNAME", (0, 0), (-1, -1), self.font_regular),
                ("FONTNAME", (0, 0), (0, -1), self.font_bold),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#64748b")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ])
        )
        story.append(info_table)
        story.append(Spacer(1, 10 * mm))

        story.append(Paragraph("Bo'limlar bo'yicha ballar:", label))
        story.append(Spacer(1, 3 * mm))

        skill_rows = [
            ["Bo'lim", "Ball"],
            ["Reading", f"{result.reading_ball:.1f}"],
            ["Listening", f"{result.listening_ball:.1f}"],
            ["Writing", f"{result.writing_ball:.1f}"],
            ["Speaking", f"{result.speaking_ball:.1f}"],
        ]
        skill_table = Table(skill_rows, colWidths=[80 * mm, 80 * mm])
        skill_table.setStyle(
            TableStyle([
                ("FONTSIZE", (0, 0), (-1, -1), 10.5),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, -1), self.font_regular),
                ("FONTNAME", (0, 0), (-1, 0), self.font_bold),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ])
        )
        story.append(skill_table)
        story.append(Spacer(1, 10 * mm))

        overall_rows = [
            ["Umumiy ball", f"{result.overall_score:.1f}"],
            ["CEFR darajasi", result.cefr_level or "-"],
        ]
        overall_table = Table(overall_rows, colWidths=[80 * mm, 80 * mm])
        overall_table.setStyle(
            TableStyle([
                ("FONTSIZE", (0, 0), (-1, -1), 12),
                ("FONTNAME", (0, 0), (-1, -1), self.font_bold),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#0f172a")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f5f9")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ])
        )
        story.append(overall_table)

        doc.build(
            story,
            onFirstPage=self._header_footer,
            onLaterPages=self._header_footer,
        )
        return buf.getvalue()

class AnswerSheetPDFService(_BasePDFService):
    """
    Javoblar varaqasini generatsiya qiladi.

    Bo'sh varaq shablon sifatida `static/templates/javoblar_varaqasi_shablon.pdf`
    dan olinadi. Bu shablonga DTM logotipi va asl footer havolasi ENWIS brendi
    bilan BIR MARTA, doimiy ravishda almashtirilgan (fayl darajasida tahrirlangan,
    har generatsiyada emas). Runtime'da faqat 2 ta dinamik hudud overlay qilinadi:

      1. Sarlavha qatori -> "{imtihon nomi} | F.I.Sh. (CEFR daraja)"
      2. "Maxsus kod" o'rni -> talaba ID sini kodlovchi QR kod

    Qolgan barcha elementlar (ramkalar, savol raqamlari, variant doirachalari,
    EXAMPLE katakchalari, qism yorliqlari, ENWIS logotipi, footer havolasi)
    shablon faylning o'zida saqlanadi va bu servis ularga tegmaydi.
    """

    TEMPLATE_PATH = str(STATIC_DIR / "templates" / "javoblar_varaqasi_shablon.pdf")

    # Shablonda dinamik to'ldiriladigan hududlar (150dpi renderdan o'lchab
    # aniqlangan, PDF punktlarida, sahifa Letter: 612x792pt, origin quyi-chap):
    _TITLE_BOX = dict(x0=152, x1=470, y0=716, y1=748)
    _CODE_BOX = dict(x0=302, y0=572, x1=402, y1=714)

    @staticmethod
    def _white_out(c, box):
        c.setFillColor(colors.white)
        c.rect(box["x0"], box["y0"], box["x1"] - box["x0"], box["y1"] - box["y0"], stroke=0, fill=1)

    def _draw_title(self, c, user_full_name: str, cefr_code: str, exam_title: str):
        box = self._TITLE_BOX
        title_text = f"{exam_title} | {user_full_name} (CEFR {cefr_code or '-'})"
        cx = (box["x0"] + box["x1"]) / 2
        cy = (box["y0"] + box["y1"]) / 2 - 5
        avail_w = (box["x1"] - box["x0"]) - 4
        font_name = self.font_bold
        font_size = 20
        while c.stringWidth(title_text, font_name, font_size) > avail_w and font_size > 8:
            font_size -= 0.5
        c.setFillColor(colors.HexColor("#e6291c"))
        c.setFont(font_name, font_size)
        c.drawCentredString(cx, cy, title_text)

    def _draw_qr(self, c, student_id: str):
        import qrcode

        box = self._CODE_BOX
        w = box["x1"] - box["x0"]
        h = box["y1"] - box["y0"]

        payload = student_id or "-"
        qr = qrcode.QRCode(border=1, box_size=8)
        qr.add_data(payload)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="#e6291c", back_color="white").convert("RGB")

        qr_buf = BytesIO()
        qr_img.save(qr_buf, format="PNG")
        qr_buf.seek(0)

        size = min(w, h - 14)
        qx = box["x0"] + (w - size) / 2
        qy = box["y0"] + h - size - 6
        c.drawImage(ImageReader(qr_buf), qx, qy, width=size, height=size)

        c.setFillColor(colors.HexColor("#e6291c"))
        c.setFont("Helvetica-Bold", 7.5)
        c.drawCentredString(box["x0"] + w / 2, box["y0"] + 4, f"ID: {student_id or '-'}")

    def generate_answer_sheet(
        self,
        user_full_name: str,
        cefr_code: str,
        exam_date: str,
        exam_title: str = "Mock imtihon",
        student_id: str | None = None,
    ) -> bytes:
        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas

        # Source archive has the answer-sheet master inside the module package;
        # production may instead mount the customized static template path.
        template_path = Path(self.TEMPLATE_PATH)
        if not template_path.is_file():
            packaged_template = Path(__file__).resolve().parents[1] / "paper" / "templates_answer_sheet.pdf"
            if packaged_template.is_file():
                template_path = packaged_template
            else:
                raise FileNotFoundError(
                    f"Answer sheet template topilmadi: {self.TEMPLATE_PATH} yoki {packaged_template}"
                )
        base_reader = PdfReader(str(template_path))
        base_page = base_reader.pages[0]
        page_w = float(base_page.mediabox.width)
        page_h = float(base_page.mediabox.height)

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=(page_w, page_h))

        self._white_out(c, self._TITLE_BOX)
        self._white_out(c, self._CODE_BOX)
        self._draw_title(c, user_full_name, cefr_code, exam_title)
        self._draw_qr(c, student_id or cefr_code)

        c.showPage()
        c.save()
        buf.seek(0)

        overlay_reader = PdfReader(buf)
        base_page.merge_page(overlay_reader.pages[0])

        writer = PdfWriter()
        writer.add_page(base_page)
        out = BytesIO()
        writer.write(out)
        return out.getvalue()