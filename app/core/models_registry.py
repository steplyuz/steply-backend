"""Barcha ORM model modullarini bir joyda import qiladi.

`Base.metadata` to'liq bo'lishi uchun (Alembic autogenerate/check, init_db, testlar)
shu modul import qilinadi. Yangi model moduli qo'shilsa, FAQAT shu yerga qo'shing.
"""
from app.modules.auth import models as _auth_models  # noqa: F401
from app.modules.admin import system_settings as _system_settings  # noqa: F401
from app.modules.exams.mock import models as _mock_models  # noqa: F401
from app.modules.tests.reading import models as _reading_models  # noqa: F401
from app.modules.tests.listening import models as _listening_models  # noqa: F401
from app.modules.tests.writing import models as _writing_models  # noqa: F401
from app.modules.tests.speaking import models as _speaking_models  # noqa: F401
from app.modules.exams.paper import models as _paper_models  # noqa: F401
from app.modules.billing import models as _billing_models  # noqa: F401
from app.modules.exams.auth import models as _exams_auth_models  # noqa: F401
