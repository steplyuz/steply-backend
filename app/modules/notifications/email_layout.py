"""Email uchun HTML: Steply brendi bilan o'ralgan, email klientlar uchun mos (jadval + inline CSS).

Admin faqat xabarning ichki qismini (HTML yoki matn) yozadi, sarlavha, rang va footer avtomatik qo'shiladi.
Xavfsizlik: script, iframe, style, on...= hodisalari va javascript: havolalari rad etiladi.
"""
from __future__ import annotations

import html as _html
import re

BRAND_DARK = "#0F3D26"
BRAND_LIME = "#C8FF00"
BG = "#F1F5F2"

_FORBIDDEN = re.compile(
    r"<\s*(script|iframe|object|embed|style|link|meta|form)\b|javascript\s*:|\son[a-z]+\s*=",
    re.IGNORECASE,
)


def validate_html(fragment: str | None) -> None:
    """Admin yozgan HTML'da xavfli teg yoki atribut bo'lsa ValueError."""
    if fragment and _FORBIDDEN.search(fragment):
        raise ValueError("HTML ichida ruxsat etilmagan teg yoki atribut bor (script, iframe, style, form, on...= hodisalari)")


def text_to_html(text: str | None) -> str:
    """Oddiy matnni HTML'ga: HTML belgilari escape qilinadi, yangi qatorlar <br> bo'ladi."""
    return _html.escape(text or "").replace("\n", "<br>")


def wrap(inner_html: str, unsubscribe_url: str | None = None) -> str:
    """Ichki HTML'ni Steply brendli to'liq email hujjatiga o'raydi."""
    unsub = ""
    if unsubscribe_url:
        unsub = (
            '<br><a href="' + _html.escape(unsubscribe_url, quote=True) + '" style="color:#6b7b72;">'
            "Obunani bekor qilish</a>"
        )
    return (
        '<!doctype html><html lang="uz"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        f'<body style="margin:0;padding:0;background:{BG};font-family:Arial,Helvetica,sans-serif;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};padding:24px 12px;">'
        '<tr><td align="center">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        'style="max-width:600px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;">'
        f'<tr><td style="background:{BRAND_DARK};padding:20px 28px;">'
        '<span style="font-size:22px;font-weight:700;color:#ffffff;letter-spacing:-0.02em;">Steply</span>'
        f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{BRAND_LIME};margin-left:6px;"></span>'
        '</td></tr>'
        '<tr><td style="padding:28px;color:#0F2A1C;font-size:15px;line-height:1.6;">'
        + inner_html +
        '</td></tr>'
        '<tr><td style="padding:18px 28px;background:#F7FAF8;color:#6b7b72;font-size:12px;line-height:1.5;">'
        'Steply — milliy sertifikatga tayyorgarlik platformasi. steply.uz'
        + unsub +
        '</td></tr>'
        '</table></td></tr></table></body></html>'
    )
