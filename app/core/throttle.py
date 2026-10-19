"""Umumiy (worker'lar o'rtasida) muvaffaqiyatsizlik hisoblagichi.

Login parollari, exams ikkinchi omili kabi joylarda "N marta xato → vaqtincha blok"
mantig'i kerak. Oldin bu xotirada (dict) edi: har worker o'z hisobini yuritardi.
Endi `limits` kutubxonasining storage'i ishlatiladi — `memory://` yoki `redis://`
(RATE_LIMIT_STORAGE_URI), ya'ni slowapi bilan bir xil backend.
"""
from functools import lru_cache

from limits.storage import Storage, storage_from_string

from app.core.config import settings


@lru_cache(maxsize=4)
def get_storage(uri: str | None = None) -> Storage:
    return storage_from_string(uri or settings.RATE_LIMIT_STORAGE_URI)


class FailureThrottle:
    """`max_failures` ta xatodan keyin `window_seconds` davomida blok.

    Kalit (key) ichki prefiks bilan saqlanadi, shuning uchun bir storage'da bir nechta
    throttle bir-biriga xalaqit bermaydi.
    """

    def __init__(
        self,
        namespace: str,
        max_failures: int,
        window_seconds: int,
        storage: Storage | None = None,
    ) -> None:
        self.namespace = namespace
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._storage = storage

    @property
    def storage(self) -> Storage:
        return self._storage or get_storage()

    def _key(self, key: str) -> str:
        return f"throttle:{self.namespace}:{key}"

    def is_blocked(self, key: str) -> bool:
        return (self.storage.get(self._key(key)) or 0) >= self.max_failures

    def record_failure(self, key: str) -> None:
        # Qat'iy oyna: birinchi xatodan boshlab sanaladi, har xatoda uzaytirilmaydi.
        self.storage.incr(self._key(key), self.window_seconds)

    def reset(self, key: str) -> None:
        self.storage.clear(self._key(key))
