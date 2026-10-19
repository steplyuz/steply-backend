"""Strict master-template based Reading/Listening paper renderer.

The master PDF is never re-drawn.  It is used as the page background and only
its content slots are covered and re-filled.  This keeps the official header,
part rules, typography geometry, margins, page numbers and graphical assets
stable.  Content is intentionally constrained to fixed slots; if it does not
fit, generation fails instead of producing a different-looking paper.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.utils import ImageReader

W, H = A4
ROOT = Path(__file__).resolve().parents[4]
TEMPLATE_DIR = ROOT / "static" / "templates"
READING_MASTER = TEMPLATE_DIR / "READING_MASTER.pdf"
LISTENING_MASTER = TEMPLATE_DIR / "LISTENING_MASTER.pdf"

class TemplateFitError(ValueError):
    pass

BODY = ParagraphStyle("TemplateBody", fontName="Helvetica", fontSize=10.5, leading=15, alignment=TA_LEFT)
BODY_ITALIC = ParagraphStyle("TemplateItalic", parent=BODY, fontName="Helvetica-Oblique")
BOLD = ParagraphStyle("TemplateBold", parent=BODY, fontName="Helvetica-Bold")
BOLD_ITALIC = ParagraphStyle("TemplateBoldItalic", parent=BODY, fontName="Helvetica-BoldOblique")
SMALL = ParagraphStyle("TemplateSmall", fontName="Helvetica", fontSize=9.2, leading=12.3, alignment=TA_LEFT)
SMALL_ITALIC = ParagraphStyle("TemplateSmallItalic", parent=SMALL, fontName="Helvetica-Oblique")
CARD = ParagraphStyle("TemplateCard", fontName="Helvetica", fontSize=9.2, leading=12.0, alignment=TA_LEFT)
CARD_BOLD = ParagraphStyle("TemplateCardBold", parent=CARD, fontName="Helvetica-Bold")


def _esc(s: Any) -> str:
    import html
    return html.escape(str(s or "")).replace("\n", "<br/>")


def _asset_path(value: Any) -> str | None:
    """Resolve a local /static/... URL to the backend filesystem path."""
    if not value:
        return None
    p = str(value)
    if p.startswith("/static/"):
        return str(ROOT / p.lstrip("/"))
    return p if Path(p).exists() else None


def _text(c, text, x, top, width, height, style=BODY, *, center=False, bold=False, italic=False):
    if bold and italic: style = BOLD_ITALIC
    elif bold: style = BOLD
    elif italic: style = BODY_ITALIC
    if center:
        style = ParagraphStyle("center", parent=style, alignment=TA_CENTER)
    # Fixed template slots are strict, but ordinary long passages should be
    # reduced slightly instead of failing immediately. We never change the
    # slot geometry; only the text size/leading is reduced within safe limits.
    base_size = float(style.fontSize)
    base_leading = float(style.leading)
    chosen = None
    for factor in (1.00, 0.94, 0.88, 0.82, 0.76, 0.72, 0.68, 0.64, 0.60):
        size = max(7.0, base_size * factor)
        leading = max(8.5, base_leading * factor)
        trial = ParagraphStyle(
            f"slot_{id(style)}_{factor}",
            parent=style,
            fontSize=size,
            leading=leading,
        )
        p = Paragraph(_esc(text), trial)
        aw, ah = p.wrap(width, height)
        if ah <= height + 0.1:
            chosen = (p, ah)
            break
    if chosen is None:
        raise TemplateFitError(f"Template slotga matn sig'madi: {str(text)[:90]!r}")
    p, ah = chosen
    p.drawOn(c, x, H - top - ah)
    return ah


def _white(c, x, top, width, height):
    c.saveState(); c.setFillColor(colors.white); c.rect(x, H-top-height, width, height, fill=1, stroke=0); c.restoreState()


def _rect(c, x, top, width, height, fill, stroke=None, line=0):
    c.saveState(); c.setFillColor(fill)
    if stroke is not None: c.setStrokeColor(stroke); c.setLineWidth(line)
    else: c.setStrokeColor(fill); c.setLineWidth(0)
    c.rect(x, H-top-height, width, height, fill=1, stroke=1 if stroke else 0)
    c.restoreState()




def _merge_overlay(master: Path, overlay: BytesIO) -> bytes:
    overlay.seek(0)
    bg = PdfReader(str(master))
    ov = PdfReader(overlay)
    out = PdfWriter()
    if len(bg.pages) != len(ov.pages):
        raise TemplateFitError(f"Template page soni mos emas: {len(bg.pages)} vs {len(ov.pages)}")
    for bp, op in zip(bg.pages, ov.pages):
        bp.merge_page(op)
        out.add_page(bp)
    buf = BytesIO(); out.write(buf); return buf.getvalue()




def _open_overlay(page_count):
    # Build lazily so each page can be drawn in order.
    buf = BytesIO(); c = canvas.Canvas(buf, pagesize=A4)
    return buf, c


def _finalize(c, buf):
    c.save(); return buf


def _parts(exam):
    return sorted(exam.get("parts") or [], key=lambda p: (p.get("part_number") if p.get("part_number") is not None else 999))


def _qsorted(p):
    return sorted(p.get("questions") or [], key=lambda q: q.get("question_number", 0))


def _qtext(q): return q.get("text") or q.get("question") or ""


def _options(q):
    out=[]
    for o in q.get("options") or []:
        if isinstance(o, str): out.append((o, o))
        else: out.append((o.get("label", ""), o.get("value", "")))
    return out




def _extract_labeled_blocks(passage: str, prefix: str, labels: str = "ABCDEFGHIJ") -> dict[str, str]:
    import re
    text = str(passage or "")
    section = text
    if prefix and prefix in text:
        section = text.split(prefix, 1)[1]
    stop = re.search(r"\n\s*(?:RESTAURANT TEXTS|TEXTS|QUESTIONS|PASSAGE)\b", section, re.I)
    if stop:
        section = section[:stop.start()]
    out = {}
    for m in re.finditer(r"(?:^|\n)\s*([A-J])\.\s*(.*?)(?=\n\s*[A-J]\.\s*|$)", section, re.S):
        out[m.group(1)] = " ".join(m.group(2).split())
    return out


def _extract_numbered_blocks(passage: str, start: int, end: int) -> dict[int, str]:
    import re
    text = str(passage or "")
    out = {}
    for m in re.finditer(r"(?:^|\n)\s*(\d+)\.\s*(.*?)(?=\n\s*\d+\.\s*|$)", text, re.S):
        n = int(m.group(1))
        if start <= n <= end:
            out[n] = " ".join(m.group(2).split())
    return out


def _extract_paragraphs(passage: str) -> tuple[str, str, list[str]]:
    """Return title, intro and I-VI paragraphs from the compact Part 3 passage."""
    import re
    text = str(passage or "").strip()
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    title = blocks[0] if blocks else ""
    intro = blocks[1] if len(blocks) > 1 else ""
    paras = []
    for b in blocks[2:]:
        if re.match(r"^[IVX]+\.", b): paras.append(b)
    return title, intro, paras


def render_reading_template(exam: dict) -> bytes:
    if not READING_MASTER.exists(): raise FileNotFoundError(str(READING_MASTER))
    ps = _parts(exam); pmap = {i+1:p for i,p in enumerate(ps)}
    if len(ps) != 5: raise TemplateFitError("READING template aynan 5 ta Part talab qiladi")
    buf,c = _open_overlay(9)

    # Page 1 is intentionally left intact: official cover/instructions are part of the master.
    c.showPage()

    # P2 — Part 1 + Part 2 statements
    p1,p2=pmap[1],pmap[2]
    _white(c, 40, 90, 520, 272)
    _text(c, p1.get("title") or "", 43, 96, 510, 18, BOLD, center=True)
    _text(c, p1.get("passage") or "", 43, 121, 512, 235, BODY)
    _white(c, 40, 530, 520, 200)
    opts = p2.get("options") or []
    statements = _extract_labeled_blocks(str(p2.get("passage") or ""), "STATEMENTS A-J")
    if statements:
        opts = [{"label": k, "value": v} for k, v in sorted(statements.items())]
    if not opts:
        seen=[]
        for q in _qsorted(p2):
            for o in _options(q):
                if o not in seen: seen.append(o)
        opts=[{"label":a,"value":b} for a,b in seen]
    # The clean master reserves A-E / F-J in two columns.
    for i, o in enumerate(opts[:10]):
        col = 0 if i < 5 else 1
        row = i if i < 5 else i - 5
        x = 52 if col == 0 else 306
        y = 539 + row * 35
        _text(c, f"{o.get('label','')}) {o.get('value','')}", x, y, 238, 30, SMALL)
    c.showPage()

    # P3 — eight fixed advert/card slots.
    cards=[
      (68,54,187,110,"#DDF58D"),(329,54,176,110,"#9ED9EA"),
      (68,170,198,95,"#C8C1E7"),(329,170,177,112,"#DDF58D"),
      (68,272,246,136,"#FFFFFF"),(329,272,219,160,"#F7DED4"),
      (68,408,250,136,"#FFF99A"),(329,408,219,114,"#E5D2F7")]
    advert_map = _extract_numbered_blocks(str(p2.get("passage") or ""), 7, 14)
    qs=_qsorted(p2)
    for idx,(x,top,w,h,col) in enumerate(cards):
        _rect(c,x,top,w,h,colors.HexColor(col))
        q=qs[idx] if idx<len(qs) else None
        if not q: continue
        qn=int(q.get("question_number") or 0)
        content = q.get("card_text") or q.get("advert_text") or advert_map.get(qn) or q.get("text") or ""
        _text(c, content, x+5, top+7, w-10, h-12, CARD)
    c.showPage()

    # P4 — Part 3 headings + Q15-20 + title/image intro.
    p3=pmap[3]
    _white(c, 40, 139, 520, 178)
    _text(c, "List of headings:", 257, 140, 90, 15, BOLD, center=True)
    opts=p3.get("options") or []
    if not opts and _qsorted(p3): opts=[{"label":a,"value":b} for a,b in _options(_qsorted(p3)[0])]
    y=165
    for o in opts[:8]:
        ah=_text(c, f"{o.get('label','')}) {o.get('value','')}", 188, y, 280, 17, SMALL_ITALIC)
        y += 20
    # Question labels are fixed by template.
    _white(c, 40, 325, 160, 90)
    for i,q in enumerate(_qsorted(p3)[:6]):
        _text(c, f"{q.get('question_number')}.  Paragraph {chr(73+i)}", 43, 326+i*16, 150, 15, SMALL)
    # Intro image area and title/preamble; image can optionally be supplied.
    _white(c, 65, 420, 170, 120)
    image_path = _asset_path(p3.get("image_path") or p3.get("image_url"))
    if image_path:
        try:
            c.drawImage(ImageReader(image_path), 65, H-540, width=170, height=112, preserveAspectRatio=True, anchor='c', mask='auto')
        except Exception as e:
            raise TemplateFitError(f"Part 3 image yuklanmadi: {e}")
    _white(c, 210, 418, 350, 140)
    parsed_title, parsed_intro, parsed_paras = _extract_paragraphs(str(p3.get("passage") or ""))
    _text(c, p3.get("title") or parsed_title or "", 313, 446, 245, 18, BOLD, center=True)
    pre = p3.get("intro") or p3.get("context") or parsed_intro
    if pre: _text(c, pre, 218, 470, 338, 72, BODY_ITALIC)
    _white(c, 40, 589, 516, 164)
    paras = p3.get("paragraphs") or parsed_paras
    for i,para in enumerate(paras[:2]):
        _text(c, f"{['I.','II.'][i]}", 43, 590+i*86, 25, 18, BODY)
        _text(c, para, 78, 589+i*86, 477, 76, BODY)
    c.showPage()

    # P5 — Part 3 continuation (III-VI)
    _white(c,40,31,516,720)
    _, _, parsed_paras = _extract_paragraphs(str(p3.get("passage") or ""))
    paras = p3.get("paragraphs") or parsed_paras
    for i,para in enumerate(paras[2:6]):
        top=31+i*118
        _text(c, f"{['III.','IV.','V.','VI.'][i]}", 43, top, 28, 18, BODY)
        _text(c, para, 78, top, 478, 105, BODY)
    c.showPage()

    # P6 — Part 4 passage
    p4=pmap[4]
    _white(c,40,107,516,686)
    _text(c,p4.get("title") or "",276,78,70,18,BOLD,center=True)
    _text(c,p4.get("passage") or "",43,107,512,680,BODY)
    c.showPage()

    # P7 — Q21-29. Compact fixed layout so all 9 questions remain on page 7.
    _white(c,40,31,520,738)
    qs=_qsorted(p4)
    _text(c,"For questions 21-24, choose the correct answer A, B, C, or D. Mark your answers on the answer sheet.",43,34,512,22,BOLD_ITALIC)
    qtops=[67,175,283,391]
    for q, top in zip(qs[:4], qtops):
        _text(c,f"{q.get('question_number')}. {_qtext(q)}",43,top,512,28,SMALL)
        opts=_options(q)[:4]
        oy=top+31
        for lab,val in opts:
            _text(c,f"{lab}) {val}",58,oy,490,15,SMALL); oy+=16
    _text(c,"For questions 25-29, decide if the following statements agree with the information given in the text. Mark your answers on the answer sheet.",43,505,512,30,BOLD_ITALIC)
    for i,q in enumerate(qs[4:9]):
        top=545+i*48
        _text(c,f"{q.get('question_number')}. {_qtext(q)}",43,top,512,20,SMALL)
        _text(c,"A) True    B) False    C) No Information",58,top+22,490,15,SMALL)
    c.showPage()

    # P8 — Part 5 passage
    p5=pmap[5]
    _white(c,40,103,516,690)
    _text(c,p5.get("title") or "",241,78,118,18,BOLD,center=True)
    _text(c,p5.get("passage") or "",43,103,512,690,BODY)
    c.showPage()

    # P9 — Q30-35 compact fixed layout.
    _white(c,40,56,520,735)
    qs=_qsorted(p5)
    _text(c,"For questions 30-33, fill in the missing information in the numbered spaces.",43,58,512,20,BOLD_ITALIC)
    _text(c,"Write no more than ONE WORD and / or A NUMBER for each question.",43,81,512,20,BOLD_ITALIC)
    for i,q in enumerate(qs[:4]):
        _text(c,f"{q.get('question_number')}. {_qtext(q)}",43,113+i*28,512,22,SMALL)
    _text(c,"For questions 34-35, choose the correct answer A, B, C, or D. Mark your answers on the answer sheet.",43,235,512,22,BOLD_ITALIC)
    for i,q in enumerate(qs[4:6]):
        top=270+i*105
        _text(c,f"{q.get('question_number')}. {_qtext(q)}",43,top,512,25,SMALL)
        oy=top+27
        for lab,val in _options(q)[:4]:
            _text(c,f"{lab}) {val}",58,oy,490,16,SMALL); oy+=17
    c.showPage()
    return _merge_overlay(READING_MASTER,_finalize(c,buf))


def render_listening_template(exam: dict) -> bytes:
    """Render Listening into the supplied 6-page clean master template.

    The master contains all official/static geometry (cover, instructions,
    separators, map, page numbers, answer labels). We only fill content slots.
    No answer keys are ever rendered.
    """
    if not LISTENING_MASTER.exists():
        raise FileNotFoundError(str(LISTENING_MASTER))

    ps = _parts(exam)
    if len(ps) != 6:
        raise TemplateFitError("LISTENING template aynan 6 ta Part talab qiladi")
    pmap = {i + 1: p for i, p in enumerate(ps)}

    buf, c = _open_overlay(6)

    # ------------------------------------------------------------------
    # PAGE 1 — exact master cover, untouched
    # ------------------------------------------------------------------
    c.showPage()

    # ------------------------------------------------------------------
    # PAGE 2 — Part 1: fixed question numbers + A/B/C labels in master.
    # We fill only the option values.
    # ------------------------------------------------------------------
    p1 = pmap[1]
    qs = _qsorted(p1)[:8]
    y_rows = {
        1: (257.0, 273.9, 293.0),
        2: (324.8, 340.1, 355.4),
        3: (390.9, 406.2, 421.5),
        4: (457.0, 472.3, 487.6),
        5: (523.0, 538.3, 553.6),
        6: (589.1, 604.4, 619.7),
        7: (654.4, 669.7, 685.0),
        8: (720.3, 735.6, 750.9),
    }
    for idx, q in enumerate(qs, 1):
        rows = y_rows[idx]
        opts = _options(q)
        for j in range(3):
            value = opts[j][1] if j < len(opts) else ""
            if value:
                _text(c, value, 165, rows[j] - 1, 360, 16, BODY)
    c.showPage()

    # ------------------------------------------------------------------
    # PAGE 3 — Part 2 blank content area + Part 3 matching list.
    # ------------------------------------------------------------------
    p2, p3 = pmap[2], pmap[3]

    # Part 2: if an image/card is supplied, use it; otherwise typeset the
    # six question prompts into the intentionally blank content area.
    p2_image = _asset_path(
        p2.get("image_path") or p2.get("image_url") or p2.get("content_image")
    )
    if p2_image:
        try:
            _white(c, 115, 190, 365, 335)
            c.drawImage(
                ImageReader(p2_image),
                125,
                H - 515,
                width=345,
                height=315,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )
        except Exception as e:
            raise TemplateFitError(f"Listening Part 2 image yuklanmadi: {e}")
    else:
        # White only the dynamic slot; Part 3 heading/instructions stay intact.
        _white(c, 55, 188, 485, 330)
        passage = str(p2.get("passage") or p2.get("context") or "").strip()
        if passage:
            _text(c, passage, 70, 195, 455, 300, BODY)
        else:
            y = 205
            for q in _qsorted(p2)[:6]:
                text = _qtext(q)
                if not text:
                    text = "Fill in the missing information."
                _text(c, f"{q.get('question_number')}. {text}", 75, y, 450, 40, BODY)
                y += 48

    # Part 3: master already has 15-18 and A-F labels. Fill their text only.
    _white(c, 136, 694, 160, 78)  # clear only the speaker text, keep fixed numbers
    for i, q in enumerate(_qsorted(p3)[:4]):
        top = 697 + i * 16.5
        _text(c, _qtext(q) or f"Speaker {i+1} …", 138, top, 155, 16, BODY)

    opts = p3.get("options") or []
    if not opts and _qsorted(p3):
        opts = [{"label": a, "value": b} for a, b in _options(_qsorted(p3)[0])]
    for i, o in enumerate(opts[:6]):
        _text(c, str(o.get("value") or ""), 318, 680 + i * 16.5, 230, 16, BODY)
    c.showPage()

    # ------------------------------------------------------------------
    # PAGE 4 — Part 4 map is fixed in the clean master. Fill only 19-23.
    # ------------------------------------------------------------------
    p4 = pmap[4]
    if p4.get("map_image"):
        _white(c, 65, 210, 485, 350)
        try:
            map_path = _asset_path(p4.get("map_image"))
            if not map_path:
                raise FileNotFoundError(str(p4.get("map_image")))
            c.drawImage(
                ImageReader(map_path),
                70,
                H - 550,
                width=470,
                height=330,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )
        except Exception as e:
            raise TemplateFitError(f"Listening Part 4 map_image yuklanmadi: {e}")

    qs = _qsorted(p4)[:5]
    qtops = [548.4, 573.4, 598.5, 623.5, 648.5]
    for q, top in zip(qs, qtops):
        _text(c, _qtext(q), 218, top, 330, 20, BODY)
    c.showPage()

    # ------------------------------------------------------------------
    # PAGE 5 — Part 5: preserve grey Extract bars, replace only Question
    # placeholders and fill option values.
    # ------------------------------------------------------------------
    p5 = pmap[5]
    block_tops = {
        24: (149.2, 170.0, 188.3, 206.6),
        25: (238.5, 254.3, 270.6, 290.1),
        26: (342.9, 363.7, 382.0, 400.3),
        27: (432.2, 452.3, 470.3, 488.0),
        28: (537.3, 557.7, 575.7, 593.6),
        29: (626.6, 646.3, 663.5, 680.7),
    }
    for q in _qsorted(p5)[:6]:
        n = int(q.get("question_number") or 0)
        if n not in block_tops:
            continue
        qtop, a_top, b_top, c_top = block_tops[n]
        # Keep number + option labels from the master; clear only the
        # placeholder question/value areas.
        _white(c, 40, qtop - 2, 515, 21)
        _text(c, str(n), 42, qtop - 1, 38, 19, BOLD)
        _text(c, _qtext(q), 58, qtop - 1, 495, 19, BOLD)
        for j, (top, (_, value)) in enumerate(zip((a_top, b_top, c_top), _options(q)[:3])):
            _white(c, 40, top - 1, 515, 17)
            _text(c, chr(65 + j) + ")", 42, top - 1, 30, 17, BODY)
            _text(c, value, 58, top - 1, 495, 17, BODY)
    c.showPage()

    # ------------------------------------------------------------------
    # PAGE 6 — Part 6: title + lecture notes. The final instructions and
    # page number are fixed by the master and remain untouched.
    # ------------------------------------------------------------------
    p6 = pmap[6]
    _white(c, 220, 118, 160, 28)  # clears "Title"
    title = str(p6.get("title") or "").strip()
    passage = str(p6.get("passage") or "").strip()
    if passage:
        lines = passage.splitlines()
        if lines and lines[0].strip() and len(lines[0].strip()) < 100 and (not title or title.lower().replace(" ", "") == "part6"):
            title = lines[0].strip()
            passage = "\n".join(lines[1:]).strip()
    if not title:
        title = "Part 6"
    _text(c, title, 160, 121, 275, 24, BOLD, center=True)

    _white(c, 55, 150, 485, 615)
    if passage:
        _text(c, passage, 70, 158, 455, 590, BODY)
    else:
        y = 165
        for q in _qsorted(p6)[:6]:
            _text(c, f"{q.get('question_number')}. {_qtext(q)}", 70, y, 455, 55, BODY)
            y += 70
    c.showPage()

    return _merge_overlay(LISTENING_MASTER, _finalize(c, buf))



WRITING_MASTER = TEMPLATE_DIR / "WRITING_MASTER.pdf"


def render_writing_template(exam: dict) -> bytes:
    """Render Writing on the supplied 3-page master without reflowing its design."""
    if not WRITING_MASTER.exists():
        raise FileNotFoundError(str(WRITING_MASTER))
    tasks = sorted(exam.get("tasks") or [], key=lambda t: (int(t.get("part_number") or 99), int(t.get("sub_part") or 0), int(t.get("id") or 0)))
    if not tasks:
        raise TemplateFitError("Writing examda tasklar mavjud emas")
    part1 = [t for t in tasks if int(t.get("part_number") or 0) == 1]
    part2 = [t for t in tasks if int(t.get("part_number") or 0) == 2]
    if not part1 or not part2:
        raise TemplateFitError("Writing template Task 1 va Task 2 ni talab qiladi")

    buf, c = _open_overlay(3)
    # Page 1: preserve official cover but correct the source typo "Reading Paper".
    _white(c, 40, 330, 515, 42)
    _text(c, "The Writing Paper consists of TWO tasks.", 42, 334, 510, 24, BODY)
    c.showPage()

    # Page 2: Task 1.  The master is a blank writing page except for its title/page number.
    _white(c, 38, 90, 520, 685)
    y = 112
    first = part1[0]
    context = str(first.get("context_text") or "").strip()
    if context:
        _text(c, context, 43, y, 510, 150, BOLD)
        y += 165
    for t in part1:
        sub = t.get("sub_part")
        label = f"Task 1.{sub}" if sub else "Task 1"
        _text(c, label, 43, y, 510, 24, BOLD); y += 30
        topic = str(t.get("topic") or "").strip()
        instruction = str(t.get("instruction") or "").strip()
        if instruction:
            _text(c, instruction, 43, y, 510, 120, BOLD); y += 125
        if topic:
            _text(c, topic, 43, y, 510, 170, BODY); y += 175
        fmt = t.get("format") or {}
        if fmt.get("min_words"):
            mx = fmt.get("max_words")
            info = f"Write about {fmt['min_words']}-{mx} words." if mx else f"Write about {fmt['min_words']} words."
            _text(c, info, 43, y, 510, 25, BODY); y += 30
        y += 12
    c.showPage()

    # Page 3: Task 2.
    _white(c, 38, 90, 520, 685)
    y = 112
    for t in part2:
        context = str(t.get("context_text") or "").strip()
        topic = str(t.get("topic") or "").strip()
        instruction = str(t.get("instruction") or "").strip()
        if context:
            _text(c, context, 43, y, 510, 170, BOLD); y += 180
        if instruction:
            _text(c, instruction, 43, y, 510, 150, BOLD); y += 155
        if topic:
            _text(c, topic, 43, y, 510, 250, BODY); y += 255
        fmt = t.get("format") or {}
        if fmt.get("min_words"):
            mx = fmt.get("max_words")
            info = f"Write {fmt['min_words']}-{mx} words." if mx else f"Write {fmt['min_words']} words."
            _text(c, info, 43, y, 510, 25, BODY); y += 30
        y += 10
    c.showPage()
    return _merge_overlay(WRITING_MASTER, _finalize(c, buf))
