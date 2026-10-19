"""Listening savol qog'ozi (PAPER 1: LISTENING) — DTM formatidagi tuzilma.

Master PDF shart emas. Umumiy yordamchilar (shrift, uslub, rasm, sahifa raqami)
paper_common.py da. correct_answer qog'ozga chiqarilmaydi (faqat example_json
ichidagi answer bundan mustasno).
"""
from __future__ import annotations

import json
from io import BytesIO
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    CondPageBreak, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
)

from .paper_common import _canvas_factory, _esc, _fonts, _image_flow, _option_lines, _styles

GAP_TYPES = {"GAP_FILL", "SENTENCE_COMPLETION", "SHORT_ANSWER"}
DOT_TYPES = {"MATCHING", "MAP_DIAGRAM"}

# Sarlavha va yakuniy matnlar — brendingizga moslang
HEADER_LINES = ["STEPLY — ONLINE EXAM PLATFORM", "ENGLISH LISTENING PRACTICE"]
DIRECTIONS = [
    "You will hear each recording twice.",
    "Each question carries ONE mark.",
    "Total listening time: approx. 35 minutes.",
    "At the end of the Listening Paper, you will have to transfer your answers to the "
    "Answer Sheet. You will be allowed 10 minutes extra time to do this.",
    "FOLLOW THE INSTRUCTIONS OF THE INVIGILATORS!",
]
END_LINES = [
    "THIS IS END OF THE LISTENING PAPER.",
    "TRANSFER YOUR ANSWERS ON THE ANSWER SHEET.",
    "YOU HAVE TEN MINUTES TO DO SO.",
]


def _example_flow(raw: Any, st: dict) -> list:
    if not raw:
        return []
    try:
        ex = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError):
        return []
    flow = [Paragraph("<b>Example:</b>", st["label"])]
    if ex.get("text"):
        flow.append(Paragraph(_esc(ex["text"]), st["opt"]))
    if ex.get("question"):
        flow.append(Paragraph(f"0. {_esc(ex['question'])}", st["opt"]))
    flow += _option_lines(ex.get("options") or [], st)
    if ex.get("answer"):
        flow.append(Paragraph(f"Correct answer: {_esc(ex['answer'])}", st["instr"]))
    return [KeepTogether(flow)]


def _question_flow(q: dict, st: dict) -> list:
    n = q.get("question_number")
    text = (q.get("text") or "").strip()
    qtype = str(q.get("type") or "").upper()
    own_options = q.get("options") or []

    if qtype in GAP_TYPES:
        # Bo'sh joy: matnda (n) yoki ____ bor bo'lsa o'sha joyda, aks holda oxirida
        if f"({n})" in text or "____" in text:
            body = _esc(text)
        else:
            body = f"{_esc(text)} ({n}) ........................".strip()
        return [KeepTogether([Paragraph(body, st["q"])])]

    prefix = f"<b>{_esc(n)}.</b> " if n is not None else ""
    line = f"{prefix}{_esc(text)}".rstrip()
    if qtype in DOT_TYPES and text and not own_options:
        line += " ........................"  # Part 3 / Part 4: ro'yxatdan tanlanadi
    return [KeepTogether([Paragraph(line, st["q"])] + _option_lines(own_options, st))]


def _part_flow(part: dict, st: dict) -> list:
    flow: list = [CondPageBreak(60 * mm), Paragraph(f"Part {_esc(part.get('part_number'))}", st["part"])]
    if part.get("instruction"):
        flow.append(Paragraph(_esc(part["instruction"]), st["instr"]))
    flow += _example_flow(part.get("example_json"), st)
    flow += _image_flow(part.get("image_url") or part.get("map_image"))
    if part.get("context"):
        flow.append(Paragraph(f"<b>{_esc(part['context'])}</b>", st["body"]))
    if part.get("passage"):
        flow.append(Paragraph(_esc(part["passage"]), st["body"]))

    shared = part.get("options") or []  # Part 3 (A-F) va Part 4 (A-H) ro'yxati — bir marta
    if shared:
        flow.append(KeepTogether(_option_lines(shared, st)))

    last_group = None
    questions = sorted(part.get("questions") or [], key=lambda x: int(x.get("question_number") or 0))
    for q in questions:
        group = q.get("group_title")  # Part 5: "Extract One" va hk.
        if group and group != last_group:
            flow.append(Paragraph(f"<b>{_esc(group)}</b>", st["label"]))
            last_group = group
        flow += _question_flow(q, st)
    return flow


def _cover_flow(exam: dict, parts: list, st: dict) -> list:
    flow: list = [Paragraph(_esc(line), st["header"]) for line in HEADER_LINES]
    flow += [
        Spacer(1, 4 * mm),
        Paragraph("PAPER 1: LISTENING", st["title"]),
        Paragraph(_esc(exam.get("title") or ""), st["sub"]),
        Paragraph(f"Language: English · CEFR {_esc(exam.get('cefr_level'))}", st["sub"]),
        Spacer(1, 6 * mm),
        Paragraph(f"The Listening Paper consists of {len(parts)} parts.", st["body"]),
    ]
    for p in parts:
        nums = [int(q.get("question_number") or 0) for q in p.get("questions") or []]
        if nums:
            flow.append(Paragraph(f"Part {_esc(p.get('part_number'))}: Questions {min(nums)}-{max(nums)}", st["body"]))
    flow.append(Spacer(1, 3 * mm))
    flow += [Paragraph(_esc(line), st["body"]) for line in DIRECTIONS]
    flow += [
        Spacer(1, 8 * mm),
        Paragraph("Please write your full name here:", st["body"]),
        Paragraph("______________________________________ (Candidate's full name)", st["body"]),
        Spacer(1, 4 * mm),
        Paragraph("Please sign here:", st["body"]),
        Paragraph("________________ (Signature)", st["body"]),
    ]
    return flow


def _ending_flow(st: dict) -> list:
    return [Spacer(1, 8 * mm)] + [Paragraph(_esc(line), st["header"]) for line in END_LINES]


def render_listening_paper(exam: dict) -> bytes:
    regular, bold = _fonts()
    st = _styles(regular, bold)
    parts = sorted(exam.get("parts") or [], key=lambda p: int(p.get("part_number") or 0))

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Listening — {exam.get('title') or 'Paper 1'}", author="Steply",
    )
    story = _cover_flow(exam, parts, st) + [PageBreak()]
    for part in parts:
        story += _part_flow(part, st)
    story += _ending_flow(st)
    doc.build(story, canvasmaker=_canvas_factory(regular))
    return buf.getvalue()