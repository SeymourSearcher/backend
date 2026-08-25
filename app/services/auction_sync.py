import asyncio
import logging
from datetime import timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.dialects.mysql import insert

from app.clock import utcnow
from app.database import async_session
from app.models import ActiveAuction, EndedAuction, Bid
from app.services.matching import match_columns
from app.services.auctions import (
    get_all_auctions,
    get_auctions_ended,
    parse_active_auction,
    parse_ended_auction,
    SeymourAuction,
    SeymourEndedAuction,
)

logger = logging.getLogger(__name__)

ACTIVE_SYNC_INTERVAL = 120
ENDED_SYNC_INTERVAL = 30
STALE_THRESHOLD_MINUTES = 10
BATCH_SIZE = 500


async def upsert_active_auctions_batch(db: AsyncSession, auctions: list[SeymourAuction]):
    if not auctions:
        return

    now = utcnow()
    matches = match_columns([a.hex_color for a in auctions])
    values = [
        {
            "auction_id": a.auction_id,
            "seller_uuid": a.seller,
            "price": a.price,
            "is_bin": a.is_bin,
            "start_time": a.start_time,
            "end_time": a.end_time,
            "item_uuid": a.item_uuid,
            "hex_color": a.hex_color,
            "piece": a.piece,
            "last_updated": now,
            **matches[a.hex_color.upper()],
        }
        for a in auctions
    ]

    stmt = insert(ActiveAuction).values(values)
    stmt = stmt.on_duplicate_key_update(
        price=stmt.inserted.price,
        end_time=stmt.inserted.end_time,
        last_updated=stmt.inserted.last_updated,
    )
    await db.execute(stmt)


async def upsert_ended_auctions_batch(db: AsyncSession, auctions: list[SeymourEndedAuction]):
    if not auctions:
        return

    matches = match_columns([a.hex_color for a in auctions])
    values = [
        {
            "auction_id": a.auction_id,
            "seller_uuid": a.seller,
            "buyer_uuid": a.buyer,
            "price": a.price,
            "is_bin": a.is_bin,
            "item_uuid": a.item_uuid,
            "hex_color": a.hex_color,
            "piece": a.piece,
            "timestamp": a.timestamp,
            **matches[a.hex_color.upper()],
        }
        for a in auctions
    ]

    stmt = insert(EndedAuction).values(values)
    stmt = stmt.on_duplicate_key_update(auction_id=stmt.inserted.auction_id)
    await db.execute(stmt)


async def sync_bids_batch(db: AsyncSession, auctions: list[SeymourAuction]):
    auctions_with_bids = [a for a in auctions if not a.is_bin and a.bids]
    if not auctions_with_bids:
        return
    
    auction_ids = [a.auction_id for a in auctions_with_bids]
    
    existing = await db.execute(
        select(Bid.auction_id, Bid.timestamp).where(Bid.auction_id.in_(auction_ids))
    )
    existing_by_auction: dict[str, set[int]] = {}
    for auction_id, timestamp in existing.fetchall():
        existing_by_auction.setdefault(auction_id, set()).add(timestamp)
    
    new_bids = []
    for auction in auctions_with_bids:
        existing_timestamps = existing_by_auction.get(auction.auction_id, set())
        for b in auction.bids:
            if b.timestamp not in existing_timestamps:
                new_bids.append({
                    "auction_id": auction.auction_id,
                    "bidder_uuid": b.bidder_uuid,
                    "amount": b.amount,
                    "timestamp": b.timestamp,
                })
    
    if new_bids:
        await db.execute(insert(Bid).values(new_bids))


async def sync_active_auctions(db: AsyncSession) -> set[str]:
    raw_auctions = await get_all_auctions()
    
    parsed_auctions = []
    for raw in raw_auctions:
        parsed = parse_active_auction(raw)
        if parsed:
            parsed_auctions.append(parsed)
    
    active_ids = {a.auction_id for a in parsed_auctions}
    
    for i in range(0, len(parsed_auctions), BATCH_SIZE):
        batch = parsed_auctions[i:i + BATCH_SIZE]
        await upsert_active_auctions_batch(db, batch)
        await sync_bids_batch(db, batch)
    
    return active_ids


async def sync_ended_auctions(db: AsyncSession):
    data = await get_auctions_ended()
    raw_ended = data.get("auctions", [])

    parsed_auctions = []
    for raw in raw_ended:
        parsed = parse_ended_auction(raw)
        if parsed:
            parsed_auctions.append(parsed)

    ended_ids = {a.auction_id for a in parsed_auctions}

    for i in range(0, len(parsed_auctions), BATCH_SIZE):
        batch = parsed_auctions[i:i + BATCH_SIZE]
        await upsert_ended_auctions_batch(db, batch)

    if ended_ids:
        await db.execute(
            delete(ActiveAuction).where(ActiveAuction.auction_id.in_(ended_ids))
        )

    stale_cutoff = utcnow() - timedelta(minutes=STALE_THRESHOLD_MINUTES)
    await db.execute(
        delete(ActiveAuction).where(ActiveAuction.last_updated < stale_cutoff)
    )


async def run_active_sync():
    async with async_session() as db:
        try:
            active_ids = await sync_active_auctions(db)
            await db.commit()
            logger.info(f"Active auction sync complete. {len(active_ids)} active auctions.")
            return active_ids
        except Exception as e:
            await db.rollback()
            logger.error(f"Active auction sync failed: {e}")
            raise


async def run_ended_sync():
    async with async_session() as db:
        try:
            await sync_ended_auctions(db)
            await db.commit()
            logger.info("Ended auction sync complete.")
        except Exception as e:
            await db.rollback()
            logger.error(f"Ended auction sync failed: {e}")
            raise


async def active_sync_loop():
    logger.info(f"Starting active auction sync loop (interval: {ACTIVE_SYNC_INTERVAL}s)")
    while True:
        try:
            await run_active_sync()
        except Exception as e:
            logger.error(f"Active sync loop error: {e}")
        await asyncio.sleep(ACTIVE_SYNC_INTERVAL)


async def ended_sync_loop():
    logger.info(f"Starting ended auction sync loop (interval: {ENDED_SYNC_INTERVAL}s)")
    while True:
        try:
            await run_ended_sync()
        except Exception as e:
            logger.error(f"Ended sync loop error: {e}")
        await asyncio.sleep(ENDED_SYNC_INTERVAL)


async def start_sync_loop():
    await asyncio.gather(active_sync_loop(), ended_sync_loop())
