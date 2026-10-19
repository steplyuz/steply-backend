"""Speaking savollar varag'i (PDF) — rasmiy "Namunaviy topshiriqlar" ko'rinishida.

Sahifa: A4 GORIZONTAL (landscape) — rasmlar kattaroq va o'qilishi oson.
Ishonchlilik: rasm balandligi sahifaga sig'masa, avtomatik kichraytirib qayta
quriladi (reportlab LayoutError yiqitmaydi).

Tartib (Speaking_format.pdf namunasi bo'yicha):
  * har bir Part alohida sahifada;
  * Part sarlavhasi (qalin) + ko'rsatma matni (kursiv);
  * Part 1.2 / Part 2 da rasmlar HAQIQATAN joylanadi (ilgari faqat URL matni chiqardi);
  * savollar — nuqtali ro'yxat;
  * Part 3 — mavzu sarlavhali, FOR | AGAINST ikki ustunli jadval.

Rasm manzillari faqat LOKAL static katalogdan o'qiladi (/static/... yoki shu
domenlardagi to'liq URL). Serverdan tashqi URL'ga so'rov yuborilmaydi (SSRF).
"""
from __future__ import annotations

import html
import logging
from io import BytesIO
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    LayoutError,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.config import settings
from app.core.paths import STATIC_DIR

logger = logging.getLogger(__name__)

_FAMILY = "SteplySpeaking"
_FONT_CANDIDATES = [
    # Calibri bilan metrikasi bir xil (namuna Word'da yozilgan)
    ("/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf", "/usr/share/fonts/truetype/crosextra/Carlito-Bold.ttf",
     "/usr/share/fonts/truetype/crosextra/Carlito-Italic.ttf", "/usr/share/fonts/truetype/crosextra/Carlito-BoldItalic.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"),
    ("C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf",
     "C:/Windows/Fonts/calibrii.ttf", "C:/Windows/Fonts/calibriz.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf",
     "C:/Windows/Fonts/ariali.ttf", "C:/Windows/Fonts/arialbi.ttf"),
]


def _register_fonts() -> tuple[str, str, str]:
    """(regular, bold, italic) shrift nomlari; Unicode shrift topilmasa Helvetica."""
    names = [f"{_FAMILY}", f"{_FAMILY}-Bold", f"{_FAMILY}-Italic", f"{_FAMILY}-BoldItalic"]
    if names[0] in pdfmetrics.getRegisteredFontNames():
        return names[0], names[1], names[2]
    for files in _FONT_CANDIDATES:
        if all(Path(f).is_file() for f in files):
            for name, path in zip(names, files):
                pdfmetrics.registerFont(TTFont(name, path))
            pdfmetrics.registerFontFamily(
                names[0], normal=names[0], bold=names[1], italic=names[2], boldItalic=names[3]
            )
            return names[0], names[1], names[2]
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


# ---------------------------------------------------------------------------
# Rasm
# ---------------------------------------------------------------------------
def _host(value: str) -> str:
    return (urlsplit(value).netloc or "").lower()


def resolve_local_image(url: Optional[str]) -> Optional[Path]:
    """Rasm URL'ini static katalogdagi FAYLGA aylantiradi; topilmasa None."""
    value = (url or "").strip()
    if not value:
        return None
    parsed = urlsplit(value)
    path = parsed.path
    if parsed.scheme in {"http", "https"}:
        own_hosts = {_host(settings.MEDIA_DOMAIN or ""), _host(settings.PUBLIC_BASE_URL or "")} - {""}
        if parsed.netloc.lower() not in own_hosts:
            return None  # tashqi manzil: server tomondan yuklab olinmaydi
    if not path.startswith("/static/"):
        return None
    root = STATIC_DIR.resolve()
    candidate = (root / unquote(path[len("/static/"):])).resolve()
    if root != candidate and root not in candidate.parents:
        return None  # ../ orqali static'dan chiqib ketish
    return candidate if candidate.is_file() else None


def _image_flowable(url: Optional[str], max_w: float, max_h: float):
    """Rasm Flowable'i (proporsiya saqlanadi) yoki None."""
    path = resolve_local_image(url)
    if path is None:
        return None
    try:
        from PIL import Image as PILImage

        with PILImage.open(path) as im:
            im.load()
            if im.mode in {"RGBA", "LA", "P"}:
                im = im.convert("RGBA")
                bg = PILImage.new("RGB", im.size, "white")
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")
            im.thumbnail((1600, 1600))  # PDF hajmini cheklash
            width, height = im.size
            buf = BytesIO()
            im.save(buf, format="JPEG", quality=88)
        scale = min(max_w / width, max_h / height)
        if scale <= 0:
            return None
        buf.seek(0)
        return Image(buf, width=width * scale, height=height * scale)
    except Exception:  # buzuq/qo'llab-quvvatlanmagan rasm butun PDF'ni yiqitmasin
        logger.warning("Speaking PDF: rasm o'qilmadi: %s", url, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Matnlar
# ---------------------------------------------------------------------------
def _esc(value: Any) -> str:
    return html.escape(str(value or "")).replace("\r\n", "\n").replace("\n", "<br/>")


def _spoken(seconds: int) -> str:
    """30 -> '30 seconds', 60 -> 'one minute', 120 -> 'two minutes'."""
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
    if seconds and seconds % 60 == 0:
        m = seconds // 60
        return f"{words.get(m, m)} minute{'s' if m != 1 else ''}"
    return f"{seconds} seconds"


def _intro_1_1(p) -> str:
    return (
        "Part one. In this part, I’m going to ask you three short questions about yourself and your interests. "
        "And then, you will see some photos and answer some questions about them. "
        f"You will have {_spoken(p.answer_seconds)} to reply to each question. "
        "Begin speaking when you hear this sound (chime)."
    )


def _intro_1_2(p) -> str:
    return (
        "Now, I’m going to ask you to compare two pictures and I will ask you two questions about them. "
        "Look at the photographs."
    )


def _intro_2(p) -> str:
    return (
        "Part two. In this part, I’m going to show you a picture and ask you three questions. "
        f"You will have {_spoken(p.prep_seconds)} to think about your answers before you start speaking. "
        f"You will have {_spoken(p.answer_seconds)} to answer all three questions. "
        "Begin speaking when you hear this sound (chime). Look at the photograph."
    )


def _intro_3(p) -> str:
    return (
        f"Part three. In this part, you are going to speak on a topic for {_spoken(p.answer_seconds)}. "
        "You can see the topic on the screen and two lists of points - for and against - related to the topic. "
        "Choose two items from each list and give a balanced argument to represent both sides of the topic. "
        f"You have {_spoken(p.prep_seconds)} to prepare your argument. "
        f"You will then have {_spoken(p.answer_seconds)} to speak. "
        "Begin speaking when you hear this sound (chime)."
    )


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
PAGE_SIZE = landscape(A4)  # 297 x 210 mm
_MARGIN_X = 16 * mm
_MARGIN_Y = 14 * mm
# Rasm balandligi uchun urinishlar (mm): sig'masa keyingisiga o'tiladi.
_IMAGE_HEIGHT_TRIES_MM = (120, 95, 75, 60)


def _styles(regular: str, bold: str, italic: str) -> dict[str, ParagraphStyle]:
    ink = colors.HexColor("#111111")
    body = ParagraphStyle("SpkBody", fontName=regular, fontSize=13, leading=18, textColor=ink)
    return {
        "title": ParagraphStyle("SpkTitle", fontName=bold, fontSize=18, leading=23, textColor=ink, spaceAfter=3 * mm),
        "desc": ParagraphStyle("SpkDesc", fontName=regular, fontSize=12, leading=16, textColor=ink, spaceAfter=3 * mm),
        "part": ParagraphStyle("SpkPart", fontName=bold, fontSize=16, leading=20, textColor=ink, spaceAfter=4 * mm),
        "intro": ParagraphStyle(
            "SpkIntro", fontName=italic, fontSize=13, leading=18, textColor=ink, spaceAfter=5 * mm, alignment=TA_JUSTIFY,
        ),
        "body": body,
        "cell_head": ParagraphStyle("SpkCellHead", parent=body, fontName=bold),
        "topic": ParagraphStyle("SpkTopic", parent=body, fontName=bold, alignment=1, fontSize=14, leading=19),
        "missing": ParagraphStyle("SpkMissing", parent=body, textColor=colors.grey),
        "ink": ink,
    }


def _build_story(test, st: dict, image_h_mm: float, content_w: float, regular: str):
    ink = st["ink"]
    body = st["body"]
    gap = 8 * mm

    def bullets(items: list[str]):
        return ListFlowable(
            [ListItem(Paragraph(_esc(t), body), leftIndent=16, spaceAfter=3 * mm) for t in items if t],
            bulletType="bullet", start="•", leftIndent=16, bulletFontName=regular, bulletFontSize=12,
        )

    def image_row(urls: list[Optional[str]]):
        """Rasmlar yonma-yon (Part 1.2) yoki bitta (Part 2). Topilmagan rasm — aniq belgi."""
        count = max(1, len(urls))
        cell_w = (content_w - gap * (count - 1)) / count if count > 1 else min(content_w, 170 * mm)
        max_h = image_h_mm * mm
        cells = []
        for url in urls:
            img = _image_flowable(url, cell_w, max_h)
            if img is None:
                logger.warning("Speaking PDF: rasm topilmadi yoki o'qilmadi: %s", url)
                img = Paragraph("[image not available]", st["missing"])
            cells.append(img)
        row, widths = [], []
        for i, cell in enumerate(cells):
            if i:
                row.append("")
                widths.append(gap)
            row.append(cell)
            widths.append(cell_w)
        table = Table([row], colWidths=widths, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
        ]))
        return table

    story: list = [Paragraph(_esc(test.title), st["title"])]
    if getattr(test, "description", None):
        story.append(Paragraph(_esc(test.description), st["desc"]))

    first = True

    def new_part(label: str):
        nonlocal first
        if not first:
            story.append(PageBreak())
        first = False
        story.append(Paragraph(label, st["part"]))

    if test.part_1_1:
        p = test.part_1_1
        new_part("Part 1.1")
        story.append(Paragraph(_esc(_intro_1_1(p)), st["intro"]))
        story.append(bullets([p.question_1, p.question_2, p.question_3]))

    if test.part_1_2:
        p = test.part_1_2
        new_part("Part 1.2")
        story.append(image_row([p.image_1_url, p.image_2_url]))
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph(_esc(_intro_1_2(p)), st["intro"]))
        story.append(bullets([p.question_describe, p.question_2, p.question_3]))

    if test.part_2:
        p = test.part_2
        new_part("Part 2")
        story.append(Paragraph(_esc(_intro_2(p)), st["intro"]))
        story.append(image_row([p.image_url]))
        story.append(Spacer(1, 5 * mm))
        story.append(bullets([p.question_1, p.question_2, p.question_3]))

    if test.part_3:
        p = test.part_3
        new_part("Part 3")
        story.append(Paragraph(_esc(_intro_3(p)), st["intro"]))

        def col(points):
            return [Paragraph(f"•&nbsp;&nbsp;{_esc(x)}", body) for x in points]

        table = Table(
            [
                [Paragraph(_esc(p.topic), st["topic"]), ""],
                [Paragraph("FOR", st["cell_head"]), Paragraph("AGAINST", st["cell_head"])],
                [col(p.for_points), col(p.against_points)],
            ],
            colWidths=[content_w / 2, content_w / 2],
        )
        table.setStyle(TableStyle([
            ("SPAN", (0, 0), (1, 0)),
            ("BOX", (0, 0), (-1, -1), 0.8, ink),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, ink),
            ("LINEBELOW", (0, 1), (-1, 1), 0.8, ink),
            ("LINEAFTER", (0, 1), (0, -1), 0.8, ink),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(KeepTogether([table]))

    return story


def render_speaking_pdf(test) -> bytes:
    regular, bold, italic = _register_fonts()
    st = _styles(regular, bold, italic)
    content_w = PAGE_SIZE[0] - 2 * _MARGIN_X

    last_error: Exception | None = None
    for image_h_mm in _IMAGE_HEIGHT_TRIES_MM:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=PAGE_SIZE, leftMargin=_MARGIN_X, rightMargin=_MARGIN_X,
            topMargin=_MARGIN_Y, bottomMargin=_MARGIN_Y, title=str(test.title or "Speaking"),
        )
        try:
            doc.build(_build_story(test, st, image_h_mm, content_w, regular))
            return buffer.getvalue()
        except LayoutError as exc:  # rasm sahifaga sig'madi — kichikroq urinamiz
            last_error = exc
            logger.warning("Speaking PDF: %smm rasm balandligida sig'madi, kichraytiriladi", image_h_mm)
    raise last_error  # type: ignore[misc]