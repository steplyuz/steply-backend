"""Reading savol qog'ozi (PAPER 2: READING) — DTM formatidagi tuzilma.

Oddiy qismlar (Part 1, 4, 5): matn → savollar (guruh ko'rsatmasi bilan).
Matching qismlar (Part 2, 3): variantlar ro'yxati → savollar → matn.
Javoblar (correct_answer) qog'ozga chiqarilmaydi.
Ikki o'tish: birinchisi jami sahifa sonini aniqlaydi.
"""
from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    CondPageBreak, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from .paper_common import _canvas_factory, _esc, _fonts, _image_flow, _option_lines, _styles

GAP_TYPES = {"GAP_FILL", "GAP_FILL_FILL", "SENTENCE_COMPLETION", "SHORT_ANSWER"}
MATCH_TYPES = {"TEXT_MATCH", "HEADINGS_MATCH"}

HEADER_LINES = ["STEPLY — ONLINE EXAM PLATFORM", "ENGLISH READING PRACTICE"]
DIRECTIONS = [
    "You may write on the question paper if you wish, but you must transfer your answers to the "
    "Answer Sheet within the time limit. No extra time is allowed to do so.",
    "Each question carries ONE mark.",
    "FOLLOW THE INSTRUCTIONS OF THE INVIGILATORS!",
    "AT THE END OF THE PAPER, THE QUESTION PAPER WILL BE COLLECTED BY THE INVIGILATOR.",
    "NO MATERIALS CAN BE REMOVED FROM THE EXAMINATION ROOM.",
    "DO NOT OPEN THE QUESTION PAPER UNTIL YOU ARE TOLD TO DO SO!",
]


def _qtype(q: dict) -> str:
    return str(q.get("type") or "").upper()


def _qnum(q: dict) -> int:
    return int(q.get("question_number") or 0)


def _word_rng(a: int, b: int) -> tuple[str, str]:
    return ("question", str(a)) if a == b else ("questions", f"{a}-{b}")


def _letters(options: list) -> str:
    labels = [str(o.get("label")) for o in options if o.get("label")]
    if len(labels) <= 1:
        return labels[0] if labels else ""
    if len(labels) == 2:
        return f"{labels[0]} or {labels[1]}"
    return ", ".join(labels[:-1]) + ", or " + labels[-1]


def _same_options(questions: list) -> list | None:
    """Barcha savollarda bir xil variantlar bo'lsa, ularni bir marta chiqarish uchun qaytaradi."""
    sets = [q.get("options") or [] for q in questions]
    if sets and sets[0] and all(s == sets[0] for s in sets[1:]):
        return sets[0]
    return None


def _strip_headings_block(text: str) -> str:
    """Part 3 matnida 'List of headings:' ro'yxati bor — uni alohida chiqaramiz."""
    stripped = text.lstrip()
    if stripped.lower().startswith("list of headings"):
        chunks = stripped.split("\n\n", 1)
        return chunks[1] if len(chunks) > 1 else ""
    return text


def _passage_flow(text: str, st: dict) -> list:
    chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
    flow = []
    for i, chunk in enumerate(chunks):
        is_title = i == 0 and "\n" not in chunk and len(chunk) < 80 and not chunk.endswith(".")
        flow.append(Paragraph(_esc(chunk), st["passage_title"] if is_title else st["body"]))
    return flow


def _options_box(options: list, caption: str, st: dict) -> KeepTogether:
    cells = ([Paragraph(f"<b>{_esc(caption)}</b>", st["label"])] if caption else []) + _option_lines(options, st)
    box = Table([[cells]], colWidths=[125 * mm])
    box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return KeepTogether([box, Spacer(1, 3 * mm)])


def _group_intro(qtype: str, qs: list, st: dict) -> list:
    word, rng = _word_rng(_qnum(qs[0]), _qnum(qs[-1]))
    if qtype == "MULTIPLE_CHOICE":
        text = (f"For {word} {rng}, choose the correct answer "
                f"{_letters(qs[0].get('options') or [])}. Mark your answers on the answer sheet.")
    elif qtype == "TRUE_FALSE_NOT_GIVEN":
        text = (f"For {word} {rng}, decide if the following statements agree with the information "
                "given in the text. Mark your answers on the answer sheet.")
    elif qtype in GAP_TYPES:
        text = (f"For {word} {rng}, fill in the missing information in the numbered spaces. "
                "Write no more than ONE WORD and / or A NUMBER for each question.")
    else:
        return []
    return [Paragraph(_esc(text), st["instr"])]


def _question_flow(q: dict, qtype: str, st: dict) -> list:
    n = q.get("question_number")
    text = (q.get("text") or "").strip()
    if qtype in GAP_TYPES:
        if f"({n})" in text or "____" in text:
            body = _esc(text)
        else:
            body = f"{_esc(text)} ({n}) ........................".strip()
        return [KeepTogether([Paragraph(body, st["q"])])]

    head = Paragraph(f"<b>{_esc(n)}.</b> {_esc(text)}".rstrip(), st["q"])
    options = q.get("options") or []
    if qtype == "TRUE_FALSE_NOT_GIVEN":
        inline = "    ".join(f"{_esc(o.get('label'))}) {_esc(o.get('value'))}" for o in options)
        return [KeepTogether([head, Paragraph(inline, st["opt"])])]
    return [KeepTogether([head] + _option_lines(options, st))]


def _is_inline_gap(q: dict, passage: str) -> bool:
    """Bo'shliq matnning o'zida (masalan Part 1: '(1) ____') — alohida qator kerak emas."""
    return _qtype(q) in GAP_TYPES and f"({_qnum(q)})" in passage


def _grouped_questions_flow(questions: list, passage: str, st: dict) -> list:
    groups: list[tuple[str, list]] = []
    for q in questions:
        if _is_inline_gap(q, passage):
            continue
        t = _qtype(q)
        if groups and groups[-1][0] == t:
            groups[-1][1].append(q)
        else:
            groups.append((t, [q]))
    flow: list = []
    for t, qs in groups:
        flow += _group_intro(t, qs, st)
        for q in qs:
            flow += _question_flow(q, t, st)
    return flow


def _matching_flow(part: dict, questions: list, passage: str, st: dict) -> list:
    qtype = _qtype(questions[0])
    flow: list = []
    shared = _same_options(questions)
    if shared:
        caption = "List of headings:" if qtype == "HEADINGS_MATCH" else ""
        flow.append(_options_box(shared, caption, st))
    flow += _image_flow(part.get("image_url"))
    if qtype == "HEADINGS_MATCH" or not passage:
        for q in questions:
            n, text = q.get("question_number"), (q.get("text") or "").strip()
            line = f"<b>{_esc(n)}.</b> {_esc(text)} ........................"
            opts = [] if shared else _option_lines(q.get("options") or [], st)
            flow.append(KeepTogether([Paragraph(line, st["q"])] + opts))
    flow += _passage_flow(passage, st)
    return flow


def _part_flow(part: dict, st: dict) -> list:
    questions = sorted(part.get("questions") or [], key=_qnum)
    passage = _strip_headings_block(part.get("passage") or "")
    flow: list = [CondPageBreak(70 * mm), Paragraph(f"PART {_esc(part.get('part_number'))}", st["part"])]
    if part.get("description"):
        flow.append(Paragraph(_esc(part["description"]), st["instr"]))
    if questions and {_qtype(q) for q in questions} & MATCH_TYPES:
        return flow + _matching_flow(part, questions, passage, st)
    flow += _image_flow(part.get("image_url"))
    flow += _passage_flow(passage, st)
    flow += _grouped_questions_flow(questions, passage, st)
    return flow


def _cover_flow(exam: dict, parts: list, st: dict, pages: int | None) -> list:
    flow = [Paragraph(_esc(line), st["header"]) for line in HEADER_LINES]
    flow += [
        Spacer(1, 4 * mm),
        Paragraph("PAPER 2: READING", st["title"]),
        Paragraph(_esc(exam.get("title") or ""), st["sub"]),
        Paragraph(f"Language: English · CEFR {_esc(exam.get('cefr_level'))}", st["sub"]),
        Spacer(1, 6 * mm),
        Paragraph(f"The Reading Paper consists of {len(parts)} parts.", st["body"]),
    ]
    for p in parts:
        nums = [_qnum(q) for q in p.get("questions") or []]
        if nums:
            flow.append(Paragraph(f"Part {_esc(p.get('part_number'))}: Questions {min(nums)}-{max(nums)}", st["body"]))
    flow.append(Paragraph(f"Total time allowed: {int(exam.get('duration_minutes') or 60)} minutes", st["body"]))
    flow.append(Spacer(1, 3 * mm))
    flow += [Paragraph(_esc(line), st["body"]) for line in DIRECTIONS]
    flow += [
        Paragraph(f"The test booklet consists of {pages or '…'} printed pages.", st["body"]),
        Spacer(1, 8 * mm),
        Paragraph("Please write your full name here:", st["body"]),
        Paragraph("______________________________________ (Candidate's full name)", st["body"]),
        Spacer(1, 4 * mm),
        Paragraph("Please sign here:", st["body"]),
        Paragraph("________________ (Signature)", st["body"]),
    ]
    return flow


def _build(exam: dict, parts: list, pages: int | None) -> bytes:
    regular, bold = _fonts()
    st = _styles(regular, bold)
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Reading — {exam.get('title') or 'Paper 2'}", author="Steply",
    )
    story = _cover_flow(exam, parts, st, pages) + [PageBreak()]
    for part in parts:
        story += _part_flow(part, st)
    doc.build(story, canvasmaker=_canvas_factory(regular))
    return buf.getvalue()


def render_reading_paper(exam: dict) -> bytes:
    parts = sorted(exam.get("parts") or [], key=lambda p: int(p.get("part_number") or 0))
    first = _build(exam, parts, pages=None)
    pages = len(PdfReader(BytesIO(first)).pages)
    return _build(exam, parts, pages=pages)