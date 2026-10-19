"""MVP tayyorlik tekshiruvi (preflight). Deploy oldidan ishga tushiring.

    python scripts/preflight.py            # hisobot
    python scripts/preflight.py --strict   # ogohlantirish ham xato hisoblanadi

Chiqish kodi: 0 — bloker yo'q; 1 — BLOKER (MVP ishga tushmaydi).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@dataclass
class Report:
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ok: list[str] = field(default_factory=list)

    def block(self, msg): self.blockers.append(msg)
    def warn(self, msg): self.warnings.append(msg)
    def good(self, msg): self.ok.append(msg)


def check_templates(r: Report) -> None:
    tpl = ROOT / "static" / "templates"
    required = {
        "ANSWER_SHEET_MASTER.pdf": "OMR va javob varag'i (rasmiy A4)",
    }
    for name, purpose in required.items():
        (r.good if (tpl / name).exists() else r.block)(f"{name}: {purpose}" if (tpl / name).exists() else f"{name} yo'q — {purpose}")
    # Savol qog'ozi shablonlari (kontent egasi joylashtiradi)
    for name, purpose in {
        "READING_MASTER.pdf": "Reading qog'oz savol varag'i",
        "LISTENING_MASTER.pdf": "Listening qog'oz savol varag'i",
        "WRITING_MASTER.pdf": "Writing qog'oz savol varag'i",
    }.items():
        if (tpl / name).exists():
            r.good(f"{name}: {purpose}")
        else:
            r.block(f"{name} yo'q — {purpose} (kontent egasi joylashtirishi kerak, aks holda 503)")
    if (tpl / "javoblar_varaqasi_shablon.pdf").exists():
        r.good("javoblar_varaqasi_shablon.pdf: mock natija varaqasi")
    else:
        r.warn("javoblar_varaqasi_shablon.pdf yo'q — mock natija varaqasi PDF'i ishlamaydi")


def check_answer_keys(r: Report) -> None:
    import json

    keys = ROOT / "content" / "answer_keys"
    for name in ("listening_key.json", "reading_key.json"):
        path = keys / name
        if not path.exists():
            r.block(f"{name} yo'q (javob kaliti)")
            continue
        answers = json.loads(path.read_text(encoding="utf-8")).get("answers", {})
        if len(answers) == 35 and all(answers.get(str(i)) for i in range(1, 36)):
            r.good(f"{name}: 35 ta javob kaliti")
        else:
            r.block(f"{name}: javob kaliti to'liq emas ({len(answers)}/35)")


def check_settings(r: Report) -> None:
    from app.core.config import settings

    if not settings.is_production:
        r.warn(f"ENV={settings.ENV}: production emas — production tekshiruvi to'liq emas")
    if settings.AUTO_CREATE_SCHEMA and settings.is_production:
        r.block("AUTO_CREATE_SCHEMA=true productionda ruxsat etilmaydi (schema faqat Alembic)")
    if not settings.APPLY_CAPTCHA_ENABLED:
        r.warn("APPLY_CAPTCHA_ENABLED=false: ochiq /apply himoyasiz (productionda true)")
    elif not settings.TURNSTILE_SECRET_KEY:
        r.block("APPLY_CAPTCHA_ENABLED=true, lekin TURNSTILE_SECRET_KEY bo'sh")
    if settings.RATE_LIMIT_STORAGE_URI.startswith("memory") and settings.is_production:
        r.warn("RATE_LIMIT_STORAGE_URI=memory://: bir nechta worker bo'lsa limitlar umumiy emas (redis:// tavsiya)")
    if settings.RUN_TELEGRAM_BOT_IN_API:
        r.warn("RUN_TELEGRAM_BOT_IN_API=true: productionda bot alohida jarayon bo'lishi kerak")
    if not settings.ADMIN_ALLOWED_IPS:
        r.warn("ADMIN_ALLOWED_IPS bo'sh: admin paneli IP bilan cheklanmagan (MFA ham yo'q)")
    if not settings.OPENAI_API_KEY:
        r.warn("OPENAI_API_KEY bo'sh: Writing AI baholash ishlamaydi (qo'lda baholash kerak)")
    if settings.SMS_REGISTRATION_ENABLED if hasattr(settings, "SMS_REGISTRATION_ENABLED") else False:
        if not settings.ESKIZ_PASSWORD:
            r.block("SMS ro'yxatdan o'tish yoqilgan, lekin ESKIZ parol bo'sh")
    if settings.TELEGRAM_BOT_TOKEN:
        r.good("TELEGRAM_BOT_TOKEN sozlangan")
    else:
        r.warn("TELEGRAM_BOT_TOKEN bo'sh: Telegram ro'yxatdan o'tish ishlamaydi")
    if "localhost" in settings.TRUSTED_HOSTS and settings.is_production and len(settings.TRUSTED_HOSTS) == 1:
        r.block("TRUSTED_HOSTS faqat localhost: productionda domen(lar)ni kiriting")


def check_tools(r: Report) -> None:
    try:
        import pytesseract  # noqa: F401
        if shutil.which("tesseract"):
            r.good("Tesseract OCR: o'rnatilgan (matnli javoblar o'qiladi)")
        else:
            r.warn("Tesseract binari topilmadi: matnli javoblar qo'lda tekshiruvga tushadi")
    except ImportError:
        r.warn("pytesseract o'rnatilmagan: matnli javoblar qo'lda tekshiruvga tushadi")
    if shutil.which("pdftoppm"):
        r.good("pdftoppm (poppler): OMR va PDF render uchun mavjud")
    else:
        r.block("pdftoppm (poppler-utils) yo'q: OMR master rasmini yaratib bo'lmaydi")


def check_redis(r: Report) -> None:
    from app.core.config import settings

    uri = settings.RATE_LIMIT_STORAGE_URI
    if not uri.startswith("redis"):
        return
    try:
        import redis
        redis.Redis.from_url(uri, socket_connect_timeout=2).ping()
        r.good("Redis: ulanish muvaffaqiyatli")
    except Exception as exc:  # noqa: BLE001
        r.block(f"Redis ulanmadi ({uri}): {exc.__class__.__name__}")


def check_migrations(r: Report) -> None:
    res = subprocess.run([sys.executable, "-m", "alembic", "heads"], cwd=ROOT,
                         capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        r.block(f"alembic heads xato: {res.stderr.strip()[-200:]}")
        return
    heads = {line.split()[0] for line in res.stdout.splitlines() if line.strip()}
    if len(heads) == 1:
        r.good(f"Alembic head yagona: {next(iter(heads))}")
    else:
        r.block(f"Alembic bir nechta head: {sorted(heads)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    r = Report()
    for step in (check_templates, check_answer_keys, check_settings, check_tools, check_redis, check_migrations):
        try:
            step(r)
        except Exception as exc:  # noqa: BLE001
            r.block(f"{step.__name__} xato bilan tugadi: {exc.__class__.__name__}: {exc}")
    for msg in r.ok:
        print(f"[OK]    {msg}")
    for msg in r.warnings:
        print(f"[WARN]  {msg}")
    for msg in r.blockers:
        print(f"[BLOCK] {msg}")
    print(f"\nNatija: {len(r.ok)} OK, {len(r.warnings)} ogohlantirish, {len(r.blockers)} bloker")
    fail = bool(r.blockers) or (args.strict and bool(r.warnings))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
