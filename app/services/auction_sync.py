import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.dialects.mysql import insert

from app.clock import utcnow
from app.database import async_session
from app.dependencies import get_redis
from app.models import ActiveAuction, EndedAuction, Bid
from app.services import auction_cache
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


@dataclass(frozen=True)
class SyncChanges:
    """Which cached row sets a poll actually altered.

    Hypixel republishes auctions_ended once a minute and we poll twice, and only
    a couple of the ~130 auctions in it are ever Seymour pieces -- so most polls
    write nothing, and rebuilding the caches on every one of them would be work
    for an unchanged answer.
    """

    ended_inserted: int = 0
    active_changed: int = 0


async def _new_ids(db: AsyncSession, model, ids: set[str]) -> int:
    """How many of these auction_ids the table does not already hold.

    rowcount cannot answer this. SQLAlchemy's mysqldb-family dialects set
    CLIENT_FOUND_ROWS, so an ON DUPLICATE KEY UPDATE reports the whole batch as
    affected whether it inserted anything or not (verified on MySQL 8.4).
    """
    if not ids:
        return 0
    existing = await db.scalars(select(model.auction_id).where(model.auction_id.in_(ids)))
    return len(ids - set(existing))


async def upsert_active_auctions_batch(db: AsyncSession, auctions: list[SeymourAuction]) -> int:
    if not auctions:
        return 0

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

    inserted = await _new_ids(db, ActiveAuction, {a.auction_id for a in auctions})

    stmt = insert(ActiveAuction).values(values)
    stmt = stmt.on_duplicate_key_update(
        price=stmt.inserted.price,
        end_time=stmt.inserted.end_time,
        last_updated=stmt.inserted.last_updated,
    )
    await db.execute(stmt)
    return inserted


async def upsert_ended_auctions_batch(db: AsyncSession, auctions: list[SeymourEndedAuction]) -> int:
    if not auctions:
        return 0

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

    inserted = await _new_ids(db, EndedAuction, {a.auction_id for a in auctions})

    # Kept as an upsert rather than inserting only the new ids: the count above
    # is a separate statement, so a concurrent writer would turn the gap between
    # them into an IntegrityError.
    stmt = insert(EndedAuction).values(values)
    stmt = stmt.on_duplicate_key_update(auction_id=stmt.inserted.auction_id)
    await db.execute(stmt)
    return inserted


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


async def sync_active_auctions(db: AsyncSession) -> tuple[set[str], SyncChanges]:
    """Upsert the full active feed, then drop rows it has not refreshed lately.

    The sweep lives here, after a complete fetch, rather than on a timer: when it
    ran from the ended sync, a run of failed polls aged every row past the
    threshold and emptied the table (bids cascade with it).
    """
    raw_auctions = await get_all_auctions()

    parsed_auctions = []
    for raw in raw_auctions:
        parsed = parse_active_auction(raw)
        if parsed:
            parsed_auctions.append(parsed)

    active_ids = {a.auction_id for a in parsed_auctions}
    inserted = 0

    for i in range(0, len(parsed_auctions), BATCH_SIZE):
        batch = parsed_auctions[i:i + BATCH_SIZE]
        inserted += await upsert_active_auctions_batch(db, batch)
        await sync_bids_batch(db, batch)

    stale_cutoff = utcnow() - timedelta(minutes=STALE_THRESHOLD_MINUTES)
    swept = (await db.execute(
        delete(ActiveAuction).where(ActiveAuction.last_updated < stale_cutoff)
    )).rowcount

    return active_ids, SyncChanges(active_changed=inserted + swept)


async def sync_ended_auctions(db: AsyncSession) -> SyncChanges:
    data = await get_auctions_ended()
    raw_ended = data.get("auctions", [])

    parsed_auctions = []
    for raw in raw_ended:
        parsed = parse_ended_auction(raw)
        if parsed:
            parsed_auctions.append(parsed)

    ended_ids = {a.auction_id for a in parsed_auctions}
    inserted = 0

    for i in range(0, len(parsed_auctions), BATCH_SIZE):
        batch = parsed_auctions[i:i + BATCH_SIZE]
        inserted += await upsert_ended_auctions_batch(db, batch)

    # DELETE rowcount is the number actually removed regardless of the dialect's
    # FOUND_ROWS flag, unlike the upserts above.
    removed = 0
    if ended_ids:
        removed = (await db.execute(
            delete(ActiveAuction).where(ActiveAuction.auction_id.in_(ended_ids))
        )).rowcount

    return SyncChanges(ended_inserted=inserted, active_changed=removed)


async def _refresh_caches(db: AsyncSession, changes: SyncChanges) -> None:
    """Drop and rebuild whatever this poll invalidated. Best effort.

    Called after the commit, never before: a cache rebuilt inside the write
    transaction would publish rows a rollback then takes away. A failure here
    leaves entries stale until their TTL rather than failing the sync, which
    would drop the rows themselves.
    """
    if not (changes.ended_inserted or changes.active_changed):
        return

    try:
        redis = await get_redis()
        if changes.ended_inserted:
            await auction_cache.refresh_ended(db, redis)
        if changes.active_changed:
            await auction_cache.refresh_active(db, redis)
    except Exception:
        logger.exception("Auction cache refresh failed")


async def run_active_sync():
    async with async_session() as db:
        try:
            active_ids, changes = await sync_active_auctions(db)
            await db.commit()
        except Exception:
            await db.rollback()
            raise

        await _refresh_caches(db, changes)
        logger.info(
            f"Active auction sync complete. {len(active_ids)} active auctions, "
            f"{changes.active_changed} added or swept."
        )
        return active_ids


async def run_ended_sync():
    async with async_session() as db:
        try:
            changes = await sync_ended_auctions(db)
            await db.commit()
        except Exception:
            await db.rollback()
            raise

        await _refresh_caches(db, changes)
        logger.info(
            f"Ended auction sync complete. {changes.ended_inserted} new, "
            f"{changes.active_changed} active removed."
        )


async def _sync_forever(name: str, run, interval: int) -> None:
    logger.info(f"Starting {name} auction sync loop (interval: {interval}s)")
    while True:
        try:
            await run()
        except httpx.HTTPError as e:
            # repr, not str: httpx timeouts stringify to "".
            logger.warning(f"{name} auction sync failed: {e!r}")
        except Exception:
            logger.exception(f"{name} auction sync failed")
        await asyncio.sleep(interval)


async def start_sync_loop():
    await asyncio.gather(
        _sync_forever("Active", run_active_sync, ACTIVE_SYNC_INTERVAL),
        _sync_forever("Ended", run_ended_sync, ENDED_SYNC_INTERVAL),
    )
