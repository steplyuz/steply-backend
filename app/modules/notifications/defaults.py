"""Standart shablonlar va hodisa qoidalari (o'zbek tilida, Steply brendida).

Seed idempotent: mavjud shablon/qoida qayta yozilmaydi — admin tahrirlagan matn saqlanadi.
Har bir hodisa uchun email (HTML, brend tugmasi bilan) va ilova ichidagi (in_app) variant bor.

O'zgaruvchilar: {{name}} (qabul qiluvchi ismi, avtomatik), {{site}} (sayt manzili, avtomatik),
{{plan}}, {{end_date}}, {{days}} (hodisaga qarab yuboriladi).
"""
from __future__ import annotations

from app.modules.notifications import email_layout as el

# (code, channel, title, subject, body, html_body)
DEFAULT_TEMPLATES: list[dict] = [
    {
        "code": "welcome_email",
        "channel": "email",
        "title": "Ro'yxatdan o'tish — email",
        "subject": "Steply'ga xush kelibsiz, {{name}}!",
        "body": "Assalomu alaykum, {{name}}! Steply'ga xush kelibsiz. Hisobingiz yaratildi: {{site}}",
        "html_body": (
            el.heading("Xush kelibsiz, {{name}}!")
            + el.paragraph("Steply — milliy sertifikatga tayyorgarlik platformasi. Hisobingiz muvaffaqiyatli yaratildi.")
            + el.paragraph("Birinchi testni ishlab, bilimingizni sinab ko'ring.")
            + el.button("Platformaga o'tish", "{{site}}")
        ),
    },
    {
        "code": "welcome_inapp",
        "channel": "in_app",
        "title": "Ro'yxatdan o'tish — ilova ichida",
        "subject": "Xush kelibsiz!",
        "body": "Steply'ga xush kelibsiz, {{name}}! Birinchi testingizni boshlang.",
        "html_body": None,
    },
    {
        "code": "subscription_activated_email",
        "channel": "email",
        "title": "Obuna faollashdi — email",
        "subject": "Obunangiz faollashdi: {{plan}}",
        "body": "Hurmatli {{name}}, {{plan}} obunangiz faollashdi. Barcha imkoniyatlar ochiq: {{site}}",
        "html_body": (
            el.heading("Obunangiz faollashdi")
            + el.paragraph("Hurmatli {{name}}, {{plan}} obunangiz muvaffaqiyatli faollashdi.")
            + el.paragraph("Endi tarifga kiritilgan barcha testlar va imkoniyatlardan foydalanishingiz mumkin.")
            + el.button("Testlarni ochish", "{{site}}")
        ),
    },
    {
        "code": "subscription_activated_inapp",
        "channel": "in_app",
        "title": "Obuna faollashdi — ilova ichida",
        "subject": "Obuna faollashdi",
        "body": "{{plan}} obunangiz faollashdi. Barcha imkoniyatlar ochiq.",
        "html_body": None,
    },
    {
        "code": "subscription_expiring_email",
        "channel": "email",
        "title": "Obuna tugayapti — email",
        "subject": "Obuna muddati tugamoqda: {{days}} kun qoldi",
        "body": "Hurmatli {{name}}, {{plan}} obunangiz {{end_date}} sanada tugaydi ({{days}} kun qoldi). {{site}}",
        "html_body": (
            el.heading("Obuna muddati tugamoqda")
            + el.paragraph("Hurmatli {{name}}, {{plan}} obunangiz {{end_date}} sanada tugaydi.")
            + el.paragraph("Qolgan muddat: {{days}} kun. Uzluksiz foydalanish uchun obunani yangilang.")
            + el.button("Obunani yangilash", "{{site}}")
        ),
    },
    {
        "code": "subscription_expiring_inapp",
        "channel": "in_app",
        "title": "Obuna tugayapti — ilova ichida",
        "subject": "Obuna tugamoqda",
        "body": "{{plan}} obunangiz {{end_date}} sanada tugaydi ({{days}} kun qoldi).",
        "html_body": None,
    },
    {
        "code": "subscription_expired_email",
        "channel": "email",
        "title": "Obuna tugadi — email",
        "subject": "Obuna muddati tugadi",
        "body": "Hurmatli {{name}}, {{plan}} obunangiz {{end_date}} sanada tugadi. Qayta faollashtirish: {{site}}",
        "html_body": (
            el.heading("Obuna muddati tugadi")
            + el.paragraph("Hurmatli {{name}}, {{plan}} obunangiz {{end_date}} sanada tugadi.")
            + el.paragraph("Bilim olishni davom ettirish uchun obunani qayta faollashtiring.")
            + el.button("Obunani faollashtirish", "{{site}}")
        ),
    },
    {
        "code": "subscription_expired_inapp",
        "channel": "in_app",
        "title": "Obuna tugadi — ilova ichida",
        "subject": "Obuna tugadi",
        "body": "{{plan}} obunangiz {{end_date}} sanada tugadi.",
        "html_body": None,
    },
]

# (event_code, channel, template_code)
DEFAULT_RULES: list[tuple[str, str, str]] = [
    ("user_registered", "email", "welcome_email"),
    ("user_registered", "in_app", "welcome_inapp"),
    ("subscription_activated", "email", "subscription_activated_email"),
    ("subscription_activated", "in_app", "subscription_activated_inapp"),
    ("subscription_expiring", "email", "subscription_expiring_email"),
    ("subscription_expiring", "in_app", "subscription_expiring_inapp"),
    ("subscription_expired", "email", "subscription_expired_email"),
    ("subscription_expired", "in_app", "subscription_expired_inapp"),
]
