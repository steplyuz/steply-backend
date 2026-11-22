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
from app.modules.billing.subscription_notifications import scan_subscription_events  # noqa: E402
from app.modules.notifications.service import process_due  # noqa: E402

logger = logging.getLogger("notifications.worker")
# Obuna muddati hodisalari (ogohlantirish / tugadi) shu oraliqda tekshiriladi
SUBSCRIPTION_SCAN_INTERVAL = 900.0


async def run(once: bool, interval: float, batch: int) -> None:
    loop = asyncio.get_running_loop()
    last_scan: float | None = None
    while True:
        async with AsyncSessionLocal() as db:
            if last_scan is None or loop.time() - last_scan >= SUBSCRIPTION_SCAN_INTERVAL:
                last_scan = loop.time()
                try:
                    scan = await scan_subscription_events(db)
                    logger.info("subscription scan: %s", scan)
                except Exception:  # noqa: BLE001 — skan xatosi navbatni to'xtatmasin
                    logger.exception("subscription scan xatosi")
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
