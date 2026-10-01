import argparse
import asyncio
import logging

from app.config import get_settings
from app.database import SessionFactory
from app.services.scheduler import (
    dispatch_notifications,
    ensure_occurrences,
    process_due_occurrences,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("healthguard.worker")


async def run_cycle() -> None:
    async with SessionFactory() as db:
        created = await ensure_occurrences(db)
        processed = await process_due_occurrences(db)
        dispatched = await dispatch_notifications(db)
    logger.info(
        "worker cycle: created=%d processed=%d dispatched=%d",
        created,
        processed,
        dispatched,
    )


async def run_forever() -> None:
    poll_seconds = get_settings().worker_poll_seconds
    while True:
        try:
            await run_cycle()
        except Exception:
            logger.exception("worker cycle failed")
        await asyncio.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="HealthGuard medication reminder worker")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()
    asyncio.run(run_cycle() if args.once else run_forever())


if __name__ == "__main__":
    main()

