# To'lovlar va Mock imtihonga ro'yxatdan o'tish

Bu hujjat backend kodiga asoslangan **hozirgi** oqimni tasvirlaydi. Onlayn
to'lov shlyuzi (Payme / Click / Uzum) hozircha ulanmagan: to'lov chek orqali
amalga oshiriladi va admin tomonidan qo'lda tasdiqlanadi.

## 1. Rekvizitlar (admin, DB)

- Rekvizitlar `billing_payment_details` tizim sozlamasida saqlanadi, `.env`da emas.
- Admin yozadi: `PUT /api/v1/admin/billing/payment-details`
  (`bank_name`, `card_number`, `card_holder`, `account_number`, `phone_number`, `instructions`).
- Ochiq o'qish: `GET /api/v1/billing/payment-details`.
- Ochiq `/apply/{slug}` sahifasida pullik imtihon uchun shu rekvizitlar matn
  sifatida qaytadi. Rekvizit kiritilmagan bo'lsa maydon `null` bo'ladi —
  taxminiy karta raqami hech qachon ko'rsatilmaydi.

## 2. Mock imtihon narxi va sessiyalar

- Narx: `MockExam.price`. `0` yoki bo'sh bo'lsa imtihon bepul: sotib olish darhol faollashadi.
- Sessiya (`ExamSession`) `requires_online_payment` bayrog'iga ega:
  - `false` (standart): pullik imtihon to'lovi imtihon markazida naqd olinadi. Ro'yxatdan o'tish to'lovsiz ochiq.
  - `true`: ro'yxatdan o'tishdan oldin tasdiqlangan (`paid`) buyurtma talab qilinadi.
    Ochiq `/apply` bunday sessiyada `402` qaytaradi — akkaunt orqali kirish kerak.

## 3. Sotib olish

Ikki yo'l bor, ikkalasi ham bitta buyurtma yaratadi:

- `POST /api/v1/mock-exams/{exam_id}/buy` — `MockPurchase(is_active=false)` va `BillingOrder(mock_exam, pending_payment)`.
- `POST /api/v1/billing/orders` — `product_type: "mock_exam"`, `product_id: <exam_id>`.

Kutilayotgan buyurtma bo'lsa ikkala yo'l ham **yangi buyurtma yaratmaydi**, mavjud
yozuvni qaytaradi (idempotent).

## 4. Chek va tasdiqlash

1. Foydalanuvchi chekni yuklaydi: `POST /api/v1/billing/orders/{id}/receipt` → `under_review`.
   Chek `storage/private/billing_receipts/` ga saqlanadi (ochiq static emas).
2. Bot chekni `TELEGRAM_BILLING_ADMIN_CHAT_ID` chatiga yuboradi, tugmalar bilan.
3. Admin tugmani bosadi. Bot `TELEGRAM_BILLING_ADMIN_TELEGRAM_USER_ID` ni tekshiradi
   va `POST /billing/telegram/review` ga `reviewer_telegram_id` bilan murojaat qiladi.
   Backend shu Telegram ID ni `Identity(provider="telegram")` orqali Steply
   akkauntiga bog'laydi; akkaunt `ADMIN` yoki `SUPER_ADMIN` bo'lishi shart.
   Bog'lanmagan bo'lsa `403`.
4. Yoki web admin paneldan: `POST /api/v1/admin/billing/orders/{id}/approve`.
5. Tasdiqlash: `MockPurchase.is_active = true`. Obuna va kreditlar ham shu nuqtada beriladi.
   O'zi uchun buyurtmani tasdiqlash taqiqlangan (`403`).

Rad etish: `reject` — buyurtma `rejected` bo'ladi, `MockPurchase` faol bo'lmaydi.

## 5. Ro'yxatdan o'tish va imtihonga kirish

- Ochiq `/apply/{slug}` (login yo'q): sessiya faol bo'lsa va to'lov talab qilinmasa ro'yxatdan o'tadi (captcha `APPLY_CAPTCHA_ENABLED`).
- Akkaunt orqali `POST /api/v1/mock-exams/sessions/register`: `requires_online_payment=true` bo'lsa,
  shu imtihon uchun `paid` buyurtma kerak. Har bir `paid` buyurtma faqat bitta ro'yxatdan o'tishni tasdiqlaydi.
- Imtihonga kirish (`/api/v1/exams/*`): `MockRegistration` (bekor qilinmagan) **yoki** faol `MockPurchase` kerak.

## 6. Sozlash ketma-ketligi (production)

1. Admin akkaunt yarating. Telegram bilan bir marta kiring (Identity bog'lanadi).
2. Tasdiqlash uchun maxfiy chat/guruh yarating, botni a'zo qiling. Chat ID ni `TELEGRAM_BILLING_ADMIN_CHAT_ID` ga yozing.
3. Adminning Telegram user ID sini `TELEGRAM_BILLING_ADMIN_TELEGRAM_USER_ID` ga yozing.
4. `PUT /admin/billing/payment-details` orqali rekvizitlarni kiriting.
5. Mock imtihon narxini belgilang; sessiyalarda `requires_online_payment` ni tanlang.
6. `python scripts/preflight.py --strict` ishga tushiring: billing blokerlari yo'q bo'lishi kerak.

## 7. Ma'lum cheklovlar

- Avtomatik onlayn to'lov (Payme / Click / Uzum) yo'q. Callback, imzo tekshiruvi va merchant kalitlar kerak; ular hozircha yo'q.
- Naqd to'lov tizimda yozilmaydi. Markazdagi kassa hisobi alohida yuritiladi.
- Telegram tugmalari faqat `TELEGRAM_BILLING_ADMIN_TELEGRAM_USER_ID` va Telegram bilan bog'langan admin uchun ishlaydi.
