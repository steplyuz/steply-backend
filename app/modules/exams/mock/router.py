"""
Mock Exam — asosiy router.

Admin, User, Grading va Paper routerlari bitta joydan include qilinadi.
main.py o'zgarmaydi — faqat shu fayl yangilanadi.
"""
from fastapi import APIRouter

from .admin_router import router as admin_router
from .user_router import router as user_router
from .grading_router import router as grading_router   # YANGI: AI answer sheet grader
from .paper_router import router as paper_router
from .mvp_router import router as mvp_router, admin as mvp_admin       # YANGI: Paper PDF yuklab olish

router = APIRouter()
router.include_router(admin_router)
router.include_router(user_router)
router.include_router(grading_router)
router.include_router(paper_router)
router.include_router(mvp_router)
router.include_router(mvp_admin)
