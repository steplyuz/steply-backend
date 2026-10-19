import uuid
import enum
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    Uuid,
    Boolean, DateTime, Date, Enum, Float, ForeignKey, Integer, JSON,
    String, Text, UniqueConstraint, CheckConstraint, Index, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from app.core.mixins import ExamIdMixin, TimestampMixin

# Bog'liq modellarni import
from app.modules.tests.reading.models import ReadingTest
from app.modules.tests.listening.models import ListeningTest
from app.modules.tests.writing.models import WritingExam


# --- ENUMS ---
class SkillType(str, enum.Enum):
    READING = "READING"
    LISTENING = "LISTENING"
    WRITING = "WRITING"
    SPEAKING = "SPEAKING"


class RegistrationStatus(str, enum.Enum):
    REGISTERED = "REGISTERED"
    CHECKED_IN = "CHECKED_IN"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class AttemptStatus(str, enum.Enum):
    CREATED = "CREATED"
    EXPIRED = "EXPIRED"
    IN_PROGRESS = "IN_PROGRESS"
    PAPER_COMPLETED = "PAPER_COMPLETED"
    SPEAKING_COMPLETED = "SPEAKING_COMPLETED"
    READY_FOR_FINAL = "READY_FOR_FINAL"
    FINALIZED = "FINALIZED"


# --- MOCK EXAM ---
class MockExam(Base, ExamIdMixin, TimestampMixin):
    """4 skill (reading/listening/writing/speaking)ni birlashtiruvchi
    to'liq mock imtihon. `id`/timestamp boshqa exam modullari bilan bir
    xil mixin'dan keladi; `is_active` bu yerda ham qoladi — lekin
    is_demo/is_free/is_mock ExamFlagsMixin'dan olinmadi, chunki ular
    komponent-test (reading/listening/...) darajasiga tegishli tushuncha,
    kompozit mock-exam darajasiga emas.
    """

    __tablename__ = "mock_exams"

    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_file: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    cefr_level: Mapped[str] = mapped_column(String, default="Multilevel")
    duration_minutes: Mapped[int] = mapped_column(Integer, default=180)
    price: Mapped[int] = mapped_column(Integer, default=0)  # So'mda
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Foreign keys
    reading_id: Mapped[Optional[str]] = mapped_column(ForeignKey("reading_tests.id"), nullable=True)
    listening_id: Mapped[Optional[str]] = mapped_column(ForeignKey("listening_tests.id"), nullable=True)
    writing_id: Mapped[Optional[str]] = mapped_column(ForeignKey("writing_exams.id"), nullable=True)
    speaking_id: Mapped[str] = mapped_column(ForeignKey("speaking_tests.id"), nullable=False)

    reading_test: Mapped[Optional["ReadingTest"]] = relationship(viewonly=True, uselist=False)
    listening_test: Mapped[Optional["ListeningTest"]] = relationship(viewonly=True, uselist=False)
    writing_test: Mapped[Optional["WritingExam"]] = relationship(viewonly=True, uselist=False)
    attempts: Mapped[List["MockExamAttempt"]] = relationship(back_populates="exam", cascade="all, delete-orphan")


# --- EXAM ATTEMPT ---
class MockExamAttempt(Base):
    __tablename__ = "mock_exam_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    mock_exam_id: Mapped[str] = mapped_column(ForeignKey("mock_exams.id", ondelete="CASCADE"), nullable=False)
    registration_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("mock_registrations.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    session_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("exam_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[AttemptStatus] = mapped_column(
        Enum(AttemptStatus, native_enum=False), default=AttemptStatus.CREATED, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Server-authoritative timer snapshot. Set exactly once when an online attempt starts.
    allowed_duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    active_scope_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_finished: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        Index("ix_mock_exam_attempt_user_exam_finished", "user_id", "mock_exam_id", "is_finished"),
        Index("ix_mock_exam_attempt_session_started", "session_id", "started_at"),
    )

    exam: Mapped["MockExam"] = relationship(back_populates="attempts")
    registration: Mapped[Optional["MockRegistration"]] = relationship(foreign_keys=[registration_id])
    session: Mapped[Optional["ExamSession"]] = relationship(foreign_keys=[session_id])
    skills: Mapped[List["MockSkillAttempt"]] = relationship(back_populates="attempt", cascade="all, delete-orphan")
    result: Mapped[Optional["MockExamResult"]] = relationship(
        back_populates="attempt", uselist=False, cascade="all, delete-orphan"
    )


# --- SKILL ATTEMPT ---
class MockSkillAttempt(Base):
    __tablename__ = "mock_skill_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[int] = mapped_column(ForeignKey("mock_exam_attempts.id", ondelete="CASCADE"), nullable=False)
    skill: Mapped[SkillType] = mapped_column(Enum(SkillType, native_enum=False), nullable=False)

    raw_score: Mapped[float] = mapped_column(Float, default=0.0)
    scaled_score: Mapped[float] = mapped_column(Float, default=0.0)
    cefr_level: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_answers: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Exam UI recovery: candidate oxirgi turgan joyi va serverdagi draft.
    current_part: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    current_question: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    draft: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    last_saved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, default=None)

    # SPEAKING uchun: attempt boshlanganda 40+ ta faol SpeakingTest orasidan
    # tasodifiy tanlanib shu yerga yoziladi (bir marta tanlanadi, o'zgarmaydi).
    speaking_test_id: Mapped[Optional[str]] = mapped_column(ForeignKey("speaking_tests.id"), nullable=True)
    paper_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("exam_versions.id", ondelete="SET NULL"), nullable=True
    )

    attempt: Mapped["MockExamAttempt"] = relationship(back_populates="skills")

    __table_args__ = (
        UniqueConstraint("attempt_id", "skill", name="uq_mock_skill_attempt_attempt_skill"),
        Index("ix_mock_skill_attempt_attempt_skill", "attempt_id", "skill"),
        Index("ix_mock_skill_user_skill_test_checked", "user_id", "skill", "speaking_test_id", "is_checked"),
    )


# --- PRACTICE ATTEMPT (Reading / Listening) ---
class PracticeAttemptStatus(str, enum.Enum):
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    EXPIRED = "EXPIRED"


class PracticeAttempt(Base):
    """Practice (mock bo'lmagan) Reading/Listening urinishi.

    Ilgari practice oqimida attempt umuman yo'q edi: javoblar faqat
    submit paytida bir martalik request body'da kelardi. Endi urinish
    boshlanganda shu qator yaratiladi, javoblar `attempt_answers`da
    saqlanadi, finish natijani `reading_results`/`listening_results`ga
    yozadi va `result_id` shu natijaga ishora qiladi.
    """

    __tablename__ = "practice_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    skill: Mapped[SkillType] = mapped_column(Enum(SkillType, native_enum=False), nullable=False)
    test_id: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="FULL_PRACTICE", server_default="FULL_PRACTICE")
    part_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[PracticeAttemptStatus] = mapped_column(
        Enum(PracticeAttemptStatus, native_enum=False),
        default=PracticeAttemptStatus.IN_PROGRESS, nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Practice timer snapshot; null is retained for historical/manual attempts.
    allowed_duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    active_scope_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True, index=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_saved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # reading_results.id yoki listening_results.id (skill'ga qarab).
    result_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    answers: Mapped[List["AttemptAnswer"]] = relationship(
        back_populates="practice_attempt", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_practice_attempt_user_skill_test", "user_id", "skill", "test_id", "mode", "part_number", "status"),
    )


# --- PER-QUESTION ANSWER (mock skill attempt YOKI practice attempt) ---
class AttemptAnswer(Base):
    """Bitta savolga bitta javob. Aynan bitta urinishga tegishli:
    `mock_skill_attempt_id` YOKI `practice_attempt_id` (ikkalasi emas).

    Kalit — `question_number`: baholash va review shu raqam bo'yicha ishlaydi
    va test tahrirlanib savollar qayta yaratilganda ham o'zgarmaydi.
    `question_id` — o'sha paytdagi DB id (ma'lumot uchun).
    """

    __tablename__ = "attempt_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mock_skill_attempt_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("mock_skill_attempts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    practice_attempt_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("practice_attempts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    question_number: Mapped[int] = mapped_column(Integer, nullable=False)
    question_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    answer: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # List[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    practice_attempt: Mapped[Optional["PracticeAttempt"]] = relationship(back_populates="answers")

    __table_args__ = (
        UniqueConstraint("mock_skill_attempt_id", "question_number", name="uq_attempt_answer_mock_q"),
        UniqueConstraint("practice_attempt_id", "question_number", name="uq_attempt_answer_practice_q"),
        CheckConstraint(
            "(mock_skill_attempt_id IS NOT NULL AND practice_attempt_id IS NULL) OR "
            "(mock_skill_attempt_id IS NULL AND practice_attempt_id IS NOT NULL)",
            name="ck_attempt_answer_one_owner",
        ),
    )


# --- EXAM RESULT ---
class MockExamResult(Base):
    __tablename__ = "mock_exam_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("mock_exam_attempts.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    reading_ball: Mapped[float] = mapped_column(Float, default=0.0)
    listening_ball: Mapped[float] = mapped_column(Float, default=0.0)
    writing_ball: Mapped[float] = mapped_column(Float, default=0.0)
    speaking_ball: Mapped[float] = mapped_column(Float, default=0.0)

    overall_score: Mapped[float] = mapped_column(Float, default=0.0)
    cefr_level: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)  # PENDING | FINALIZED
    finalized_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sms_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    attempt: Mapped["MockExamAttempt"] = relationship(back_populates="result")


# --- PURCHASE ---
class MockPurchase(Base):
    __tablename__ = "mock_purchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    mock_exam_id: Mapped[str] = mapped_column(ForeignKey("mock_exams.id", ondelete="CASCADE"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    comment: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", backref="purchased_mocks")
    exam: Mapped["MockExam"] = relationship("MockExam")


# --- OCHIQ TEST SANALARI (SESSIYALAR) ---
class ExamSession(Base):
    """Bitta mock imtihonning aniq o'tkaziladigan sanasi/joyi (ochiq test sanasi)."""

    __tablename__ = "exam_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    mock_exam_id: Mapped[str] = mapped_column(ForeignKey("mock_exams.id", ondelete="CASCADE"), nullable=False)

    exam_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    center_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    room_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    location_address: Mapped[str] = mapped_column(String, nullable=False)
    region: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # imtihon o'tkaziladigan viloyat
    
    # Imtihon o'tkazilish usuli: PAPER (qog'oz + OMR) yoki COMPUTER (kompyuterda topshirish).
    delivery_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="PAPER", server_default="PAPER")

    registration_slug: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=True)

    capacity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # bo'sh qoldirilsa cheklovsiz
    # Band joylar hisoblagichi: shartli UPDATE orqali atomik oshiriladi (seats.py).
    seats_taken: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    required_documents: Mapped[str] = mapped_column(
        Text, default="Pasport yoki ID-karta;To'lov kvitansiyasi;1 dona 3x4 rasm",
    )  # ';' bilan ajratilgan ro'yxat

    # True bo'lsa, ro'yxatdan o'tishdan oldin BillingOrder(PAID) talab
    # qilinadi (qarang: registration_service.register_for_session). Admin
    # har bir sessiya uchun alohida yoqadi/o'chiradi.
    requires_online_payment: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    exam: Mapped["MockExam"] = relationship("MockExam")
    registrations: Mapped[List["MockRegistration"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


# --- FOYDALANUVCHI RO'YXATI + IMZO ---
class MockRegistration(Base):
    """
    Har bir foydalanuvchining aniq bir ExamSession'ga ro'yxatdan o'tishi.
    Imtihon kuni kelganda 'imzo' shu yerga yoziladi (attendance confirmation).
    """

    __tablename__ = "mock_registrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[int] = mapped_column(ForeignKey("exam_sessions.id", ondelete="CASCADE"), nullable=False)
    mock_exam_id: Mapped[str] = mapped_column(ForeignKey("mock_exams.id", ondelete="CASCADE"), nullable=False)

    # Ro'yxatdan o'tish paytida kiritiladigan ma'lumotlar (snapshot)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    # MK ID — talabaning SHU SEANSDAGI unikal kodi ("MK-DDMMYY-NNN").
    # Ilgari bu maydon `users_profile.mk_id` ning nusxasi (snapshot) edi;
    # endi MK ID AYNAN shu yerda, ochiq seansga yozilish momentida
    # generatsiya qilinadi (mock_test/mk_id.py) va Exams tizimiga kirish
    # shu kod orqali amalga oshadi. Shu sababli unique — ikki talaba
    # bir xil MK ID olib qolmasligi kerak.
    student_id: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    # Imtihonga kirish uchun yagona, taxmin qilib bo'lmaydigan ID.
    exam_access_id: Mapped[str] = mapped_column(String(48), nullable=False, unique=True, index=True)
    qr_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    gender: Mapped[str] = mapped_column(String, nullable=False)  # "male" | "female"
    region: Mapped[str] = mapped_column(String, nullable=False)  # yashash hududi (faqat viloyat)
    # Ikkinchi omil (MK ID bilan kirish): ochiq ro'yxatdan o'tishda telefonning oxirgi 4 raqami.
    # Shu yo'l uchun UserContact yaratilmaydi — telefon takrorlanishi va mavjudligi oshkor bo'lmaydi.
    phone_last4: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    photo_url: Mapped[str] = mapped_column(String, nullable=False)  # majburiy candidate photo

    # Qog'ozga chiqarilgan ro'yxatda foydalanuvchi qo'lda imzo qo'yadi.
    # Imtihon tugagach, admin qog'ozdagi imzoni tekshirib, shu yerda "keldi" deb belgilaydi.
    attended_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Sessiya requires_online_payment=True bo'lsa, ro'yxatdan o'tishni
    # tasdiqlagan aynan shu BillingOrder — audit va bitta to'lovni ikki marta
    # ishlatib bo'lmasligini ta'minlash uchun (qarang: registration_service).
    billing_order_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("billing_orders.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[RegistrationStatus] = mapped_column(
        Enum(RegistrationStatus, native_enum=False), default=RegistrationStatus.REGISTERED,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # bitta user bitta sessiyaga faqat bir marta ro'yxatdan o'tadi
        UniqueConstraint("user_id", "session_id", name="uq_user_session_registration"),
        Index("ix_mock_registration_session_created", "session_id", "created_at"),
        Index("ix_mock_registration_exam_created", "mock_exam_id", "created_at"),
    )

    user = relationship("User")
    session: Mapped["ExamSession"] = relationship(back_populates="registrations")
    exam: Mapped["MockExam"] = relationship("MockExam")
