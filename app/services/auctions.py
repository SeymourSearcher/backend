import httpx
import asyncio
import base64
import gzip
import io
from dataclasses import dataclass
import nbtlib

BASE_URL = "https://api.hypixel.net/v2/skyblock"

SEYMOUR_ITEMS = {
    "VELVET_TOP_HAT": 0,
    "CASHMERE_JACKET": 1,
    "SATIN_TROUSERS": 2,
    "OXFORD_SHOES": 3,
}

SEYMOUR_NAMES = {
    "Velvet Top Hat",
    "Cashmere Jacket",
    "Satin Trousers",
    "Oxford Shoes",
}


@dataclass
class AuctionBid:
    bidder_uuid: str
    amount: int
    timestamp: int


@dataclass
class SeymourAuction:
    auction_id: str
    seller: str
    price: int
    is_bin: bool
    start_time: int
    end_time: int | None
    item_uuid: str
    hex_color: str
    piece: int
    bids: list[AuctionBid] | None = None


@dataclass
class SeymourEndedAuction:
    auction_id: str
    seller: str
    buyer: str
    price: int
    is_bin: bool
    item_uuid: str
    hex_color: str
    piece: int
    timestamp: int


async def fetch_page(client: httpx.AsyncClient, page: int) -> dict:
    response = await client.get(f"{BASE_URL}/auctions", params={"page": page})
    response.raise_for_status()
    return response.json()


async def get_all_auctions() -> list[dict]:
    async with httpx.AsyncClient() as client:
        first_page = await fetch_page(client, 0)
        total_pages = first_page.get("totalPages", 1)
        all_auctions = first_page.get("auctions", [])
        
        if total_pages > 1:
            tasks = [fetch_page(client, p) for p in range(1, total_pages)]
            results = await asyncio.gather(*tasks)
            for result in results:
                all_auctions.extend(result.get("auctions", []))
        
        return all_auctions


async def get_auctions_ended() -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{BASE_URL}/auctions_ended")
        response.raise_for_status()
        return response.json()


def decode_item_bytes(item_bytes: str) -> nbtlib.Compound:
    decoded = base64.b64decode(item_bytes)
    decompressed = gzip.decompress(decoded)
    return nbtlib.File.parse(io.BytesIO(decompressed))


def rgb_to_hex(color: str) -> str:
    r, g, b = map(int, color.split(":"))
    return f"{r:02X}{g:02X}{b:02X}"


def extract_nbt_data(item_bytes: str) -> tuple[str | None, str | None, str | None]:
    try:
        nbt = decode_item_bytes(item_bytes)
        item = nbt["i"][0]
        extra = item.get("tag", {}).get("ExtraAttributes", {})
        
        item_id = str(extra.get("id", ""))
        item_uuid = str(extra.get("uuid", ""))
        
        color = extra.get("color")
        if color is None:
            return item_id, item_uuid, None
        
        hex_color = rgb_to_hex(str(color))
        return item_id, item_uuid, hex_color
    except Exception:
        return None, None, None


def parse_bids(raw_bids: list[dict]) -> list[AuctionBid]:
    return [
        AuctionBid(
            bidder_uuid=b.get("bidder", ""),
            amount=b.get("amount", 0),
            timestamp=b.get("timestamp", 0),
        )
        for b in raw_bids
    ]


def parse_active_auction(auction: dict) -> SeymourAuction | None:
    item_name = auction.get("item_name", "")
    if not any(name in item_name for name in SEYMOUR_NAMES):
        return None
    item_bytes = auction.get("item_bytes")
    if not item_bytes:
        return None
    
    item_id, item_uuid, hex_color = extract_nbt_data(item_bytes)
    if item_id not in SEYMOUR_ITEMS or not hex_color:
        return None
    
    raw_bids = auction.get("bids", [])
    highest_bid = max((b["amount"] for b in raw_bids), default=auction.get("starting_bid", 0))
    is_bin = auction.get("bin", False)
    
    return SeymourAuction(
        auction_id=auction.get("uuid", ""),
        seller=auction.get("auctioneer", ""),
        price=highest_bid,
        is_bin=is_bin,
        start_time=auction.get("start", 0),
        end_time=auction.get("end"),
        item_uuid=auction.get("item_uuid") or item_uuid or "",
        hex_color=hex_color,
        piece=SEYMOUR_ITEMS[item_id],
        bids=parse_bids(raw_bids) if not is_bin else None,
    )


def parse_ended_auction(auction: dict) -> SeymourEndedAuction | None:
    item_bytes = auction.get("item_bytes")
    if not item_bytes:
        return None
    
    item_id, item_uuid, hex_color = extract_nbt_data(item_bytes)
    if item_id not in SEYMOUR_ITEMS or not hex_color:
        return None
    
    return SeymourEndedAuction(
        auction_id=auction.get("auction_id", ""),
        seller=auction.get("seller", ""),
        buyer=auction.get("buyer", ""),
        price=auction.get("price", 0),
        is_bin=auction.get("bin", False),
        item_uuid=auction.get("item_uuid") or item_uuid or "",
        hex_color=hex_color,
        piece=SEYMOUR_ITEMS[item_id],
        timestamp=auction.get("timestamp", 0),
    )


async def get_active_seymour_auctions() -> list[SeymourAuction]:
    auctions = await get_all_auctions()
    return [a for auction in auctions if (a := parse_active_auction(auction))]


async def get_ended_seymour_auctions() -> list[SeymourEndedAuction]:
    data = await get_auctions_ended()
    auctions = data.get("auctions", [])
    return [a for auction in auctions if (a := parse_ended_auction(auction))]


if __name__ == "__main__":
    async def main():
        print("Fetching active Seymour auctions...")
        active = await get_active_seymour_auctions()
        print(f"Found {len(active)} active Seymour auctions")
        for a in active[:5]:
            print(f"  {a.hex_color} piece={a.piece} price={a.price} bin={a.is_bin}")
        
        print("\nFetching ended Seymour auctions...")
        ended = await get_ended_seymour_auctions()
        print(f"Found {len(ended)} ended Seymour auctions")
        for a in ended[:5]:
            print(f"  {a.hex_color} piece={a.piece} price={a.price}")
    
    asyncio.run(main())