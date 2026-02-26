from datetime import datetime, timedelta
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Player

CACHE_DURATION = timedelta(days=7)


async def fetch_username_from_api(uuid: str) -> str | None:
    url = f"https://api.ashcon.app/mojang/v2/user/{uuid}"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, timeout=5.0)
            if response.status_code == 200:
                return response.json().get("username")
        except httpx.RequestError:
            pass
    return None


async def get_player_name(db: AsyncSession, uuid: str) -> str | None:
    clean_uuid = uuid.replace("-", "")
    result = await db.execute(select(Player).where(Player.uuid == clean_uuid))
    player = result.scalar_one_or_none()

    if player and datetime.utcnow() - player.last_updated < CACHE_DURATION:
        return player.username

    username = await fetch_username_from_api(clean_uuid)
    if not username:
        return player.username if player else None

    if player:
        player.username = username
        player.last_updated = datetime.utcnow()
    else:
        player = Player(uuid=clean_uuid, username=username)
        db.add(player)

    await db.commit()
    return username


async def get_player_names(db: AsyncSession, uuids: list[str]) -> dict[str, str]:
    clean_uuids = {uuid.replace("-", ""): uuid for uuid in uuids}
    result = await db.execute(select(Player).where(Player.uuid.in_(clean_uuids.keys())))
    players = {p.uuid: p for p in result.scalars().all()}

    names = {}
    to_fetch = []
    now = datetime.utcnow()

    for clean, original in clean_uuids.items():
        player = players.get(clean)
        if player and now - player.last_updated < CACHE_DURATION:
            names[original] = player.username
        else:
            to_fetch.append((clean, original, player))

    if to_fetch:
        import asyncio
        fetched = await asyncio.gather(*[fetch_username_from_api(clean) for clean, _, _ in to_fetch])
        
        for (clean, original, player), username in zip(to_fetch, fetched):
            if username:
                if player:
                    player.username = username
                    player.last_updated = now
                else:
                    db.add(Player(uuid=clean, username=username))
                names[original] = username
            elif player:
                names[original] = player.username
        
        await db.commit()

    return names
