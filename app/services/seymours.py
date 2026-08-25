from typing import Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.errors import InvalidInputError, NotFoundError
from app.models import Seymour, User
from app.repositories.seymour_repo import SeymourRepository
from app.services.cache import (
    get_cached_available_filters,
    get_cached_custom_sets,
    get_cached_seymour_results,
    get_cached_user_seymours,
    invalidate_global_stats,
    invalidate_rankings,
    invalidate_user_seymours,
    set_cached_available_filters,
    set_cached_custom_sets,
    set_cached_seymour_results,
    set_cached_user_seymours,
)
from app.services.compute import FADE_DYES, SETS, find_custom_sets
from app.services.parser import parse_seymour_file
from app.services.seymour_filters import (
    compute_available_filters,
    filter_with_matches,
    sort_results,
)

MAX_UPLOAD_ROWS = 100_000


async def get_user_seymours(db: AsyncSession, redis: Redis, user_id: int) -> list[Seymour]:
    cached = await get_cached_user_seymours(redis, user_id)
    if cached is not None:
        return [Seymour(**s) for s in cached]

    seymours = await SeymourRepository(db).list_for_user(user_id)
    await set_cached_user_seymours(redis, user_id, [
        {"id": s.id, "item_name": s.item_name, "hex_color": s.hex_color, "user_id": s.user_id}
        for s in seymours
    ])
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
        return sorted(SETS.keys())
    if filter_type == "dyes":
        return sorted(FADE_DYES.keys())

    if not user:
        return []

    cached = await get_cached_available_filters(
        redis, user.id, filter_type, incomplete_only, include_dyes
    )
    if cached is not None:
        return cached

    seymours = await get_user_seymours(db, redis, user.id)
    filters = compute_available_filters(
        [(s.hex_color, s.item_name) for s in seymours], filter_type, incomplete_only, include_dyes
    )
    await set_cached_available_filters(
        redis, user.id, filter_type, incomplete_only, include_dyes, filters
    )
    return filters


async def hex_codes(
    db: AsyncSession, redis: Redis, user: User, with_pieces: bool
) -> list:
    seymours = await get_user_seymours(db, redis, user.id)
    if with_pieces:
        return list({(s.hex_color.upper(), s.item_name) for s in seymours})
    return list({s.hex_color.upper() for s in seymours})


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
    cached = await get_cached_seymour_results(
        redis, user.id, filter_type, sub_filter, incomplete_only, include_dyes
    )
    if cached is not None:
        results = sort_results(cached, sort, filter_type) if sort else cached
        return results[skip:skip + limit]

    seymours = await get_user_seymours(db, redis, user.id)

    custom_sets_data = None
    if filter_type == "custom":
        seymour_list = [(s.hex_color, s.item_name) for s in seymours]
        custom_sets_data = await get_cached_custom_sets(redis, seymour_list)
        if custom_sets_data is None:
            custom_sets_data = find_custom_sets(seymour_list, max_avg_delta_e=1.0)
            await set_cached_custom_sets(redis, seymour_list, custom_sets_data)

    filtered = filter_with_matches(
        seymours, filter_type, sub_filter, incomplete_only, include_dyes, custom_sets_data
    )
    await set_cached_seymour_results(
        redis, user.id, filter_type, sub_filter, incomplete_only, include_dyes, filtered
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

    await invalidate_global_stats(redis)
    await invalidate_user_seymours(redis, user.id)
    await invalidate_rankings(redis)

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

    await invalidate_global_stats(redis)
    await invalidate_user_seymours(redis, user.id)


async def delete_all(db: AsyncSession, redis: Redis, user: User) -> None:
    await SeymourRepository(db).delete_for_user(user.id)
    await db.commit()

    await invalidate_global_stats(redis)
    await invalidate_user_seymours(redis, user.id)
