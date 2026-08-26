"""Auction sync worker entrypoint.

Runs as its own process so that polling Hypixel and writing auction rows never
competes with request handling, and so API replicas can scale without each one
starting a duplicate sync loop. Exactly one worker should run at a time.

    python -m app.worker
"""
import asyncio
import logging
import signal
import sys

from app.services.auction_sync import start_sync_loop
from app.services.mojang import close_client
from app.services.player_sync import player_refresh_loop

logger = logging.getLogger("worker")


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )


async def main() -> None:
    loop = asyncio.get_running_loop()
    stop = loop.create_future()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: stop.done() or stop.set_result(None))
        except NotImplementedError:
            # Windows dev: fall back to KeyboardInterrupt handling below.
            pass

    sync = asyncio.create_task(asyncio.gather(start_sync_loop(), player_refresh_loop()))
    logger.info("Auction sync worker started")

    await asyncio.wait([sync, stop], return_when=asyncio.FIRST_COMPLETED)

    sync.cancel()
    try:
        await sync
    except asyncio.CancelledError:
        pass
    await close_client()
    logger.info("Auction sync worker stopped")


if __name__ == "__main__":
    configure_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
