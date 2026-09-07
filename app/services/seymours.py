from typing import Optional

from fastapi import UploadFile
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.errors import InvalidInputError, NotFoundError
from app.models import Seymour, User
from app.repositories.marketplace_repo import SellOrderRepository
from app.repositories.seymour_repo import SeymourRepository
from app.services.cache import (
    CUSTOM_SETS,
    SEYMOUR_FILTERS,
    SEYMOUR_RESULTS,
    USER_SEYMOURS,
    custom_sets_hash,
    invalidate_hex_pages,
    invalidate_user_seymours,
)
from app.services.rankings import refresh_user_stats
from app.services.compute import FADE_DYES, find_custom_sets
from app.services.matching import list_set_names
from app.services.parser import parse_seymour_file
from app.services.seymour_filters import (
    compute_available_filters,
    filter_with_matches,
    sort_results,
)

MAX_UPLOAD_ROWS = 100_000

# Comfortably above MAX_UPLOAD_ROWS in the heaviest (JSON) format, so the row
# limit is what users actually hit. Enforced while reading because the row
# count is only knowable after parsing.
MAX_UPLOAD_BYTES = 16 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024


async def read_upload(file: UploadFile) -> str:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(UPLOAD_CHUNK_BYTES):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise InvalidInputError("File is too large to upload")
        chunks.append(chunk)

    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidInputError("File must be UTF-8 encoded text")


async def get_user_seymours(db: AsyncSession, redis: Redis, user_id: int) -> list[Seymour]:
    cached = await USER_SEYMOURS.get(redis, user_id)
    if cached is not None:
        return [Seymour(**s) for s in cached]

    seymours = await SeymourRepository(db).list_for_user(user_id)
    await USER_SEYMOURS.set(redis, [
        {"id": s.id, "item_name": s.item_name, "hex_color": s.hex_color, "user_id": s.user_id}
        for s in seymours
    ], user_id)
    return seymours


async def available_filters(
    db: AsyncSession,
    redis: Redis,
    user: Optional[User],
    filter_type: str,
    incomplete_only: bool,
    include_dyes: bool,
) -> list[str]:
    if filter_type == "sets":
        return list_set_names()
    if filter_type == "dyes":
        return sorted(FADE_DYES.keys())

    if not user:
        return []

    cached = await SEYMOUR_FILTERS.get(
        redis, user.id, "filters", filter_type, incomplete_only, include_dyes
    )
    if cached is not None:
        return cached

    seymours = await get_user_seymours(db, redis, user.id)
    filters = compute_available_filters(
        [(s.hex_color, s.item_name) for s in seymours], filter_type, incomplete_only, include_dyes
    )
    await SEYMOUR_FILTERS.set(
        redis, filters, user.id, "filters", filter_type, incomplete_only, include_dyes
    )
    return filters


async def hex_codes(db: AsyncSession, redis: Redis, user: User) -> list[str]:
    seymours = await get_user_seymours(db, redis, user.id)
    return list({s.hex_color.upper() for s in seymours})


async def hex_codes_with_pieces(
    db: AsyncSession, redis: Redis, user: User
) -> list[tuple[str, int]]:
    # Seymour.item_name stores the piece index, not a name -- the router types it as Piece.
    seymours = await get_user_seymours(db, redis, user.id)
    return list({(s.hex_color.upper(), s.item_name) for s in seymours})


async def list_for_user(
    db: AsyncSession,
    redis: Redis,
    user: User,
    filter_type: str,
    sub_filter: Optional[str],
    incomplete_only: bool,
    include_dyes: bool,
    sort: Optional[str],
    skip: int,
    limit: int,
) -> list[dict]:
    cached = await SEYMOUR_RESULTS.get(
        redis, user.id, filter_type, sub_filter or "none", incomplete_only, include_dyes
    )
    if cached is not None:
        results = sort_results(cached, sort, filter_type) if sort else cached
        return results[skip:skip + limit]

    seymours = await get_user_seymours(db, redis, user.id)

    custom_sets_data = None
    if filter_type == "custom":
        seymour_list = [(s.hex_color, s.item_name) for s in seymours]
        custom_sets_data = await CUSTOM_SETS.get(redis, custom_sets_hash(seymour_list))
        if custom_sets_data is None:
            custom_sets_data = find_custom_sets(seymour_list, max_avg_delta_e=1.0)
            await CUSTOM_SETS.set(redis, custom_sets_data, custom_sets_hash(seymour_list))

    filtered = filter_with_matches(
        seymours, filter_type, sub_filter, incomplete_only, include_dyes, custom_sets_data
    )
    await SEYMOUR_RESULTS.set(
        redis, filtered, user.id, filter_type, sub_filter or "none", incomplete_only, include_dyes
    )

    results = sort_results(filtered, sort, filter_type) if sort else filtered
    return results[skip:skip + limit]


async def replace_all(
    db: AsyncSession, redis: Redis, user: User, content: str
) -> tuple[int, int]:
    parsed, skipped = parse_seymour_file(content)

    if len(parsed) > MAX_UPLOAD_ROWS:
        raise InvalidInputError("Too many seymours to upload")

    seymours = SeymourRepository(db)
    await seymours.delete_for_user(user.id)

    if parsed:
        await seymours.add_many([
            {"item_name": item.item_name, "hex_color": item.hex_color, "user_id": user.id}
            for item in parsed
        ])

    user.last_uploaded = utcnow()
    await db.commit()

    await refresh_user_stats(db, user.id)
    await invalidate_user_seymours(redis, user.id)
    await invalidate_hex_pages(redis)

    return len(parsed), skipped


async def get_owned(db: AsyncSession, user: User, seymour_id: int) -> Seymour:
    seymour = await SeymourRepository(db).get_owned(seymour_id, user.id)
    if not seymour:
        raise NotFoundError("Seymour not found")
    return seymour


async def delete_one(db: AsyncSession, redis: Redis, user: User, seymour_id: int) -> None:
    seymours = SeymourRepository(db)
    seymour = await seymours.get_owned(seymour_id, user.id)
    if not seymour:
        raise NotFoundError("Seymour not found")

    await seymours.remove(seymour)
    await db.commit()

    await refresh_user_stats(db, user.id)
    await invalidate_user_seymours(redis, user.id)
    await invalidate_hex_pages(redis)


async def delete_all(db: AsyncSession, redis: Redis, user: User) -> dict:
    """Undo an upload: every Seymour this user owns, and what derives from them.

    refresh_user_stats handles the rest -- it drops the leaderboard row and the
    featured sets when the collection comes back empty.
    """
    # Counted before the delete, not after: sell_orders.seymour_id cascades in
    # MySQL, so by the time this returns the listings are already gone and
    # nothing is left to count.
    cancelled = await SellOrderRepository(db).count_active_for_user(user.id)
    removed = await SeymourRepository(db).delete_for_user(user.id)

    # There is no uploaded file any more; leaving the stamp would date one that
    # no longer exists.
    user.last_uploaded = None
    await db.commit()

    await refresh_user_stats(db, user.id)
    await invalidate_user_seymours(redis, user.id)
    await invalidate_hex_pages(redis)

    return {"removed": removed, "sell_orders_cancelled": cancelled}
