def test_mk_id_login_is_disabled_by_default():
    # Lazy import: boshqa test modullari DATABASE_URL ni settings'dan oldin o'rnatadi.
    from app.core.config import Settings

    # MVP talabi: imtihonga faqat Exam ID bilan kiriladi.
    assert Settings.model_fields["EXAMS_MK_ID_LOGIN_ENABLED"].default is False
