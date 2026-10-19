"""Listening va Reading qog'ozlari uchun umumiy yordamchilar."""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Image, Paragraph, Spacer

ROOT = Path(__file__).resolve().parents[4]
MAX_IMG_W = 150 * mm
MAX_IMG_H = 105 * mm
_FONT_PAIRS = [
    (ROOT / "static/fonts/DejaVuSans.ttf", ROOT / "static/fonts/DejaVuSans-Bold.ttf"),
    (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
     Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
]


def _fonts() -> tuple[str, str]:
    for regular, bold in _FONT_PAIRS:
        if regular.is_file() and bold.is_file():
            if "PaperSans" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("PaperSans", str(regular)))
                pdfmetrics.registerFont(TTFont("PaperSansBold", str(bold)))
            return "PaperSans", "PaperSansBold"
    return "Helvetica", "Helvetica-Bold"


def _styles(regular: str, bold: str) -> dict[str, ParagraphStyle]:
    return {
        "header": ParagraphStyle("PHeader", fontName=bold, fontSize=11, leading=15, alignment=TA_CENTER),
        "sub": ParagraphStyle("PSub", fontName=regular, fontSize=9.5, leading=13, alignment=TA_CENTER),
        "title": ParagraphStyle("PTitle", fontName=bold, fontSize=18, leading=22, alignment=TA_CENTER,
                                spaceBefore=6 * mm, spaceAfter=6 * mm),
        "body": ParagraphStyle("PBody", fontName=regular, fontSize=10, leading=14, spaceAfter=2 * mm),
        "part": ParagraphStyle("PPart", fontName=bold, fontSize=13, leading=17,
                               spaceBefore=4 * mm, spaceAfter=2 * mm, keepWithNext=True),
        "passage_title": ParagraphStyle("PPassageTitle", fontName=bold, fontSize=12, leading=16,
                                        spaceBefore=3 * mm, spaceAfter=2 * mm, keepWithNext=True),
        "instr": ParagraphStyle("PInstr", fontName=regular, fontSize=9.5, leading=13,
                                spaceAfter=3 * mm, textColor=colors.HexColor("#374151")),
        "q": ParagraphStyle("PQ", fontName=regular, fontSize=10, leading=14,
                            spaceBefore=2 * mm, keepWithNext=True),
        "opt": ParagraphStyle("POpt", fontName=regular, fontSize=9.5, leading=13, leftIndent=8 * mm),
        "label": ParagraphStyle("PLabel", fontName=bold, fontSize=10, leading=14, spaceBefore=2 * mm),
    }


def _esc(value: Any) -> str:
    return html.escape(str(value or "")).replace("\r\n", "\n").replace("\n", "<br/>")


def _image_flow(path_value: str | None) -> list:
    if not path_value:
        return []
    path = ROOT / path_value.lstrip("/") if path_value.startswith("/static/") else Path(path_value)
    if not path.is_file():
        return []
    iw, ih = ImageReader(str(path)).getSize()
    scale = min(MAX_IMG_W / iw, MAX_IMG_H / ih, 1.0)
    return [Image(str(path), width=iw * scale, height=ih * scale), Spacer(1, 3 * mm)]


def _option_lines(options: list, st: dict) -> list:
    return [Paragraph(f"{_esc(o.get('label'))}) {_esc(o.get('value'))}", st["opt"]) for o in options]


def _canvas_factory(font: str):
    """Har sahifada 'Page X of N'."""

    class NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages: list[dict] = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self.saveState()
                self.setFont(font, 8)
                self.setFillColor(colors.HexColor("#6B7280"))
                self.drawRightString(A4[0] - 18 * mm, 9 * mm, f"Page {self._pageNumber} of {total}")
                self.restoreState()
                super().showPage()
            super().save()

    return NumberedCanvas