# Steply Backend — Mock Center API

**Versiya:** 3.0.0-mvp
**Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · Alembic · SQLite (prod hozirgi model) · argon2-cffi · PyJWT · aiogram 3

Imtihon platformasi backend'i: Reading / Listening / Writing / Speaking testlari, mock imtihon ro'yxatdan o'tish
(ochiq `/apply` va akkaunt orqali), exams kirish (MK ID + ikkinchi omil), billing (obuna va buyurtmalar),
Telegram orqali ro'yxatdan o'tish va FastAPI admin paneli.

## Tezkor ishga tushirish

```bash
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt        # testlar uchun: requirements-dev.txt

cp .env.example .env          # qiymatlarni to'ldiring (sirlarni repoga yozmang)
alembic upgrade head          # schema (AUTO_CREATE_SCHEMA=true bo'lsa init_db ham bajaradi)

uvicorn app.main:app --reload --port 8000     # API (bot tokenisiz ham ishlaydi)
python bot.py                                  # Telegram ro'yxatdan o'tish boti (alohida jarayon)
```

Production: `docs/DEPLOY.md` (jarayonlar, Redis, proxy, admin cheklovlari, majburiy `.env` qiymatlari).

## Konfiguratsiya

`app/core/config.py` dagi barcha sozlamalar `.env.example` da. `tests/test_stage8_quality.py` `.env.example` bilan `Settings` mos kelishini tekshiradi — yangi sozlama qo'shsangiz, `.env.example` ga ham qo'shing.

Asosiy kalitlar:
- `SECRET_KEY`, `ADMIN_SECRET_KEY`, `INTERNAL_API_TOKEN` — kamida 32 belgili tasodifiy satr.
- `RATE_LIMIT_STORAGE_URI` — bir nechta worker bo'lsa `redis://...`.
- `TRUST_PROXY_HEADERS` — faqat proxy ortida (aks holda IP cheklovlari soxtalashadi).
- `APPLY_CAPTCHA_ENABLED`, `EXAMS_MK_ID_REQUIRE_PHONE_LAST4` — ochiq ro'yxatdan o'tish va exams kirish.

## Migratsiyalar

```bash
alembic revision --autogenerate -m "tavsif"   # yangi revision
alembic upgrade head                           # qo'llash
alembic check                                  # model va migratsiyalar mosligi
```

Joriy head: `0008_enable_sms_registration` (SMS orqali ro‘yxatdan o‘tish standart holatda yoqilgan; admin panelidan qayta o‘chirish mumkin). Migratsiyalar Postgres'da ham ishlashi kerak (`sa.false()`,
`sa.func.now()`). Batafsil: `docs/MIGRATIONS.md`.

## Modul tuzilishi

```
app/
├── core/                 config, database, security (argon2), rate_limit (slowapi), throttle (umumiy hisoblagich),
│                         csrf, captcha (Turnstile), refresh_rotation (auth va exams/auth uchun umumiy),
│                         keys (domain-separated kalitlar), paths (absolyut yo'llar), file_storage (private fayllar)
├── modules/
│   ├── auth/             telefon/parol, Telegram OTP va OIDC, sessiyalar, refresh rotatsiya
│   ├── users/            profil, avatar
│   ├── admin/            admin API
│   ├── billing/          tariflar, buyurtmalar, tekshiruv, practice kvotasi (entitlements)
│   ├── fastadmin_panel/  /admin (FastAdmin): IP allowlist, login cheklovi, sezgir jadvallar yashirilgan
│   ├── tests/            TESTLAR: 4 skill kontenti + admin boshqaruvi (CRUD, kalitlar, PDF, versiya, lock)
│   │   ├── reading/ listening/ writing/ speaking/   (listening: yopiq audio; writing: AI draft, inson tasdiqlaydi)
│   │   ├── attempts/     umumiy urinish infratuzilmasi: common.py (baholash, javob upsert), timer.py, schemas.py, submit.py
│   │   └── content_access.py, scoring_policy.py, json_import.py
│   ├── exams/            RASMIY IMTIHON (mock markaz) — exams kirish (auth/) bilan bog'langan
│   │   ├── auth/         exams kirish (MK ID + ikkinchi omil), exams sessiyasi
│   │   ├── mock/         mock imtihon, sessiya, ochiq /apply, seats, MK ID, OMR, baholash, natijalar
│   │   ├── paper/        qog'oz varaqlari PDF
│   │   └── attempts.py   mock urinish mantiqi (javob saqlash, bo'lim finalizatsiyasi, holat)
│   └── practice/         PRACTICE — faqat is_practice=True (mock emas) testlar
│       ├── router.py     /practice/tests katalog
│       └── service.py    practice urinish mantiqi (start, javob, finish); mock-only test -> 404
bot.py                    Telegram bot (holatsiz, alohida jarayon)
migrations/               Alembic revision'lar (0001 baseline ... 0006)
scripts/backup_sqlite.py  SQLite zaxira nusxa (integrity_check, retention)
docs/                     DEPLOY.md, MIGRATIONS.md
```

## Testlar

```bash
pytest                                  # barchasi (haqiqiy DB emas: har test vaqtinchalik SQLite faylda)
pytest tests/test_stage7_billing_ai.py  # bitta modul
pytest --cov=app --cov-report=term-missing
```

Testlar `DATABASE_URL` ni o'zi vaqtinchalik faylga yo'naltiradi — `.env` dagi bazaga tegmaydi.

Redis bilan (rate-limit va umumiy throttle'ni haqiqiy storage'da tekshirish uchun):

```bash
RATE_LIMIT_TEST_REDIS_URL=redis://localhost:6379/1 RATE_LIMIT_STORAGE_URI=redis://localhost:6379/2 pytest
```

| Fayl | Nima tekshiriladi |
|---|---|
| `test_auth_hardening.py` | login enumeratsiyasi, telefon normalizatsiyasi, OTP limiti, refresh grace, CSRF, kalit ajratish |
| `test_practice_isolation.py` | practice katalogi faqat is_practice=True; mock-only testda practice urinish ochilmaydi |
| `test_hardening.py` | exams login va umumiy xavfsizlik qoidalari |
| `test_session_delivery.py` | sessiya yetkazish |
| `test_migrations.py` | migratsiyalar va modellar mosligi |
| `test_exam_flow_e2e.py` | to'liq imtihon oqimi: kontent → ro'yxatdan o'tish → kirish → javoblar → natija |
| `test_stage4_hardening.py` | bot holatsizligi, login throttle, admin cheklovlari, Redis (opsional) |
| `test_stage5_apply.py` | atomik sig'im, rasm, CAPTCHA, region, MK ID formati |
| `test_stage6_storage.py` | yo'llar, zaxira skripti, listening audio ruxsati, migratsiya 0005 |
| `test_stage7_billing_ai.py` | obuna stacking, parallel tasdiqlash, AI prompt injection va sxema |
| `test_stage8_quality.py` | argon2 moslik, exams refresh grace, konfiguratsiya, kvota |

## Ma'lum cheklovlar

- Rasmiy savol qog'ozi shablonlari (`static/templates/{READING,LISTENING,WRITING}_MASTER.pdf`) repoda yo'q.
  Ular mavjud bo'lsa, rasmiy layout ishlatiladi; shablon yo'q yoki matn sig'masa, PDF test mazmunidan muqobil layoutda yaratiladi.
- Telegram, SMS va AI provayderlari haqiqiy muhitda sinalmagan (testlar ularni almashtiradi).
- Ochiq ariza rasmlari (`static/applicant_photos/`) hali ochiq; yopiq saqlash keyingi ish.
- Admin panelda MFA yo'q; himoya — `ADMIN_ALLOWED_IPS` va login cheklovi.

## Muhim hujjatlar

- `docs/DEPLOY.md` — deploy, sozlamalar, zaxira va tiklash, ma'lum cheklovlar
- `docs/MIGRATIONS.md` — Alembic oqimi va baseline tarixi


## Unified practice and analysis quotas

- Practice uses one quota shared by Reading, Listening, Writing and Speaking.
- Analysis has its own weekly/monthly quota and is consumed only when requesting a test analysis video.
- Admin quota endpoints: `GET /api/v1/admin/analysis/limits`, `PUT /api/v1/admin/analysis/limits/practice`, and `PUT /api/v1/admin/analysis/limits/analysis`.
- Analysis endpoints: `GET /api/v1/analysis/quota`, `GET /api/v1/analysis/{skill}/{test_id}`.
- Video management: `POST/GET /api/v1/admin/analysis/videos`, `DELETE /api/v1/admin/analysis/videos/{video_id}`. Upload multipart fields are `skill`, `test_id`, `title`, and `file` (MP4/WebM/MOV, max 500 MB).
- Apply migrations with `alembic upgrade head`; the old per-skill admin quota API/table is removed by migration `0010_unified_practice_analysis`.


## Student Writing AI evaluation API

- Submit an essay: `POST /api/v1/writing-tests/{exam_id}/submit` with JSON `{"answers":[{"task_id":123,"content":"..."}]}`. The response contains the persisted `WritingResult` and its answers.
- Request AI feedback: `POST /api/v1/writing-tests/results/{result_id}/ai-check` with JSON `{"answer_id": null}`. `answer_id` may identify one answer; omit/null means evaluate all answers that do not already have AI feedback.
- Authentication uses the existing httpOnly session cookies. The result must belong to the authenticated user.
- AI feedback is available to PLUS subscribers only. `ai_limit` is measured in Writing answers, not HTTP requests. The server checks quota before calling the configured OpenAI model; saved AI feedback is the usage record, so repeating a completed request returns the saved result without a second provider call or quota use.
- Configure `OPENAI_API_KEY` and optionally `OPENAI_MODEL` in the backend environment. Without an API key, the endpoint returns HTTP 503; it does not fabricate a score.
- Common errors: `401` unauthenticated, `403` tier/entitlement denied, `404` result/answer not found or not owned by the user, `429` quota exhausted, `502` invalid/provider evaluation response, `503` AI service not configured.
- AI scoring is a draft (`evaluation_status=AI_DRAFT`, `is_finalized=false`), not a certified final grade. A teacher/admin can finalize it through `POST /api/v1/admin/writing-tests/results/{result_id}/grade`.


## Writing AI kreditlari (0011)

Writing AI limiti obuna ichidagi oylik `SubscriptionPlan.ai_limit` kvotasi va alohida sotib olinadigan `WRITING_AI_CREDITS` kreditlaridan tuziladi. Xarid mahsuloti admin katalogida yaratilganda `external_ref` maydoniga kreditlar soni (masalan, `20`) yoziladi. Foydalanuvchi `POST /api/v1/billing/orders` orqali `product_type: "writing_ai_credits"` va mahsulot ID sini yuboradi; chek yuklanib, admin buyurtmani tasdiqlagandan so'ng kreditlar faollashadi.

Migratsiya: `alembic upgrade head`. `GET /api/v1/billing/me/limits` javobidagi `ai.subscription_remaining`, `ai.purchased_remaining` va `ai.remaining` mos ravishda obuna kvotasi, xarid kreditlari va umumiy mavjud Writing AI tekshiruvlarini bildiradi. AI tekshiruv sarfi `writing_ai_usage` ledgerida answer ID bo'yicha bir martalik yoziladi.


## Billing admin Telegram review (optional)

To forward uploaded payment receipts to a private Telegram admin chat and allow inline approve/reject actions, configure these environment variables for both the API and bot process:

- `TELEGRAM_BOT_TOKEN`: bot token.
- `TELEGRAM_BILLING_ADMIN_CHAT_ID`: target private chat or controlled admin group ID.
- `TELEGRAM_BILLING_ADMIN_TELEGRAM_USER_ID`: numeric Telegram ID allowed to press the review buttons.
- `TELEGRAM_BILLING_ADMIN_USER_ID`: UUID of a real `ADMIN` or `SUPER_ADMIN` account used for audit attribution.
- `INTERNAL_API_TOKEN`: shared secret between bot and backend; use a strong random value.
- `BACKEND_URL`: API base URL ending in `/api/v1`.

Apply migrations before starting the API. Telegram review is disabled until the required settings are present. If notification delivery fails, receipt upload remains stored and the order stays in `under_review` for review in the web admin panel.

## Writing AI grading with Amazon Bedrock (OpenAI-compatible API)

The writing evaluator supports any OpenAI-compatible endpoint. For Amazon Bedrock Mantle, configure these environment variables (keep real credentials in your deployment secret store, never commit them):

```dotenv
OPENAI_API_KEY=YOUR_BEDROCK_API_KEY
OPENAI_BASE_URL=https://bedrock-mantle.us-east-2.api.aws/v1
OPENAI_MODEL=openai.gpt-oss-120b
```

The evaluator keeps the existing API, database tables, quota accounting and `AI_DRAFT` human-review state. AI feedback is stored in the existing `writing_feedbacks.content` text field as a versioned JSON string containing two separate results (Uzbek national rubric and English band rubric), criterion comments, detected language issues, corrected text, deterministic word count and underlength penalty. No schema migration is required. The two rubric scores are not averaged together. The English band rubric is an advisory estimate, not an official certificate score.

## Backend updates (2026-10)

### Separate teacher-review purchases

- Add `TEACHER_REVIEW_CREDITS` products through `POST /admin/billing/products`.
- `external_ref` is the number of reviews included in the product (1–10000).
- Create an order with `POST /billing/orders` and `product_type: "teacher_review_credits"`.
- After an admin approves the receipt, credits are granted idempotently.
- Read the current balance from `GET /billing/me/teacher-review-credits`.
- Request a paid second review with `POST /writing-tests/results/{result_id}/request-teacher-check`.

### Database migration

Run `alembic upgrade head` to create the `teacher_review_credit_grants` table and register the new billing product type. Back up the database before applying production migrations.

### Evaluation and Speaking

Writing submissions now attempt automatic AI grading when the user has enough server-side AI quota/credits for the full set of billable answers. If the quota is insufficient, the submission is retained for later evaluation; provider failures are represented as `AI_FAILED`. Writing results expose `evaluation_source` (`AI`, `TEACHER`, or `PENDING`). Reading review details include the question text and part title, and compare multi-answer responses as sets.

Speaking practice audio submission is independent from grading entitlement. Teacher grading requires a subscription that includes Speaking; unentitled users keep their audio and receive an explicit error if grading is attempted. A Speaking question-paper PDF is available at `GET /speaking-tests/practice/{test_id}/paper.pdf`, and private audio download remains restricted to the owner or an authorized evaluator.

Newly generated exam IDs and automatically generated question/part/option external IDs use UUID v4 strings. Existing IDs are retained for backward compatibility; this change does not convert every legacy integer primary key in the database. A complete primary-key conversion requires a separate data migration and coordinated API/client rollout.
