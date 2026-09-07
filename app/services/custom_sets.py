"""The homepage's featured-sets gallery: the closest self-matched sets on the site.

find_custom_sets is a KD-tree walk over one user's whole inventory, so the
gallery is served from stored rows and refreshed on upload, alongside the tallies
in app/services/rankings.py -- see that module for why the read path is not a
cache.
"""
import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_custom_set import PIECE_HEX_COLUMNS, SETS_PER_USER
from app.repositories.user_custom_set_repo import UserCustomSetRepository
from app.services.compute import find_custom_sets

logger = logging.getLogger(__name__)

# The bar the /items custom filter uses. Sets looser than this are not worth
# featuring, so the gallery shrinks rather than padding itself with them.
MAX_AVG_DELTA_E = 1.0

GALLERY_SIZE = 6
GALLERY_PER_USER = 2

# find_custom_sets grows superlinearly in inventory size -- measured on the
# bench inventories at 40ms for 5k, 353ms for 10k, 1.3s for 15k, 10.5s for 30k.
# This runs inside upload and delete requests, and MAX_UPLOAD_ROWS is 100_000,
# so it needs a ceiling rather than the caller's patience. Above it the user is
# left out of the gallery instead of holding the request open for minutes.
# Re-measure with scripts/bench_compute.py before raising.
MAX_INVENTORY = 15_000


def _rows(user_id: int, sets: list) -> list[dict]:
    """find_custom_sets output as insertable rows, ranked best first.

    Sorted here rather than trusting the order out of _greedy_select's heap,
    since set_rank is what the gallery's per-user cap selects on.
    """
    best = sorted(sets, key=lambda found: found[1])[:SETS_PER_USER]
    return [
        {
            "user_id": user_id,
            "set_rank": set_rank,
            "avg_delta_e": avg_delta_e,
            **{PIECE_HEX_COLUMNS[piece]: hex_color for piece, hex_color, _ in pieces},
        }
        for set_rank, (_, avg_delta_e, pieces) in enumerate(best)
    ]


async def refresh_user_custom_sets(
    db: AsyncSession, user_id: int, seymour_list: list[tuple[str, int]]
) -> None:
    """Recompute and store one user's featured sets. The caller commits.

    Takes the Seymour list rather than reading it, so the upload path pays for
    one query and one thread hop instead of two.

    An inventory over MAX_INVENTORY is cleared rather than skipped: skipping
    would leave the rows from when it was smaller, and those hexes may since
    have been deleted.
    """
    if len(seymour_list) > MAX_INVENTORY:
        logger.warning(
            "user %d has %d seymours, over the %d custom-set ceiling -- clearing",
            user_id, len(seymour_list), MAX_INVENTORY,
        )
        sets = []
    else:
        sets = await asyncio.to_thread(find_custom_sets, seymour_list, MAX_AVG_DELTA_E)

    await UserCustomSetRepository(db).replace_for_user(user_id, _rows(user_id, sets))


async def featured_sets(db: AsyncSession) -> list[dict]:
    return [
        {
            "discord_id": str(user.discord_id),
            "username": user.username,
            "avatar_hash": user.avatar_hash,
            "avg_delta_e": round(custom_set.avg_delta_e, 2),
            "hexes": custom_set.hexes,
        }
        for user, custom_set in await UserCustomSetRepository(db).list_best(
            GALLERY_PER_USER, GALLERY_SIZE
        )
    ]
