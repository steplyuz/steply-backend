"""Xabarnomalar worker: navbatdagi SMS/Telegram/email xabarlarni yuboradi.

Ishga tushirish (productionda alohida systemd servis sifatida):
    python scripts/process_notifications.py            # doimiy ishlaydi
    python scripts/process_notifications.py --once     # bir marta
"""
import argparse
import asyncio
import logging
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.core.models_registry  # noqa: E402,F401  (barcha modellarni ro'yxatdan o'tkazadi)
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.modules.notifications.service import process_due  # noqa: E402

logger = logging.getLogger("notifications.worker")


async def run(once: bool, interval: float, batch: int) -> None:
    while True:
        async with AsyncSessionLocal() as db:
            stats = await process_due(db, batch_size=batch)
        if stats["picked"]:
            logger.info("processed: %s", stats)
        if once:
            return
        # Navbat bo'sh bo'lsa kutamiz, band bo'lsa darhol davom etamiz
        await asyncio.sleep(1 if stats["picked"] else interval)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--batch", type=int, default=50)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run(args.once, args.interval, args.batch))


if __name__ == "__main__":
    main()
