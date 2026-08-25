"""Print the Seymour auctions currently visible upstream.

    python -m scripts.fetch_auctions

Hits the live Hypixel API. Was an __main__ block in app/services/auctions.py.
"""
import asyncio

from app.services.auctions import (
    get_active_seymour_auctions,
    get_ended_seymour_auctions,
)

PREVIEW = 5


async def main() -> None:
    active = await get_active_seymour_auctions()
    print(f"active: {len(active)}")
    for a in active[:PREVIEW]:
        print(f"  {a.hex_color} piece={a.piece} price={a.price} bin={a.is_bin}")

    ended = await get_ended_seymour_auctions()
    print(f"ended: {len(ended)}")
    for a in ended[:PREVIEW]:
        print(f"  {a.hex_color} piece={a.piece} price={a.price}")


if __name__ == "__main__":
    asyncio.run(main())
