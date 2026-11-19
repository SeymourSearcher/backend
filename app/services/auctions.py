import httpx
import asyncio
import base64
import gzip
import io
from dataclasses import dataclass
import nbtlib
import orjson

from app.pieces import ITEM_ID_TO_PIECE, ITEM_NAMES

BASE_URL = "https://api.hypixel.net/v2/skyblock"


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


# Each auctions page is ~1MB of JSON. httpx's default 5s timeout, with every page
# requested at once, failed about half of all polls in production.
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
PAGE_CONCURRENCY = 8
ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


def new_client() -> httpx.AsyncClient:
    """One per sync loop, reused across polls: a client per poll paid a fresh
    TLS handshake and SSL context load every time (~7% of worker CPU)."""
    return httpx.AsyncClient(timeout=TIMEOUT)


async def _get_json(client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = await client.get(f"{BASE_URL}/{path}", params=params)
            response.raise_for_status()
            return orjson.loads(response.content)
        except httpx.HTTPError as exc:
            if attempt == ATTEMPTS or not _retryable(exc):
                raise
            await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise AssertionError("unreachable")


def is_seymour_listing(auction: dict) -> bool:
    item_name = auction.get("item_name", "")
    return any(name in item_name for name in ITEM_NAMES)


def _seymour_listings(page: dict) -> list[dict]:
    return [a for a in page.get("auctions", []) if is_seymour_listing(a)]


async def get_all_auctions(client: httpx.AsyncClient) -> list[dict]:
    """The Seymour listings on every page of the active feed, or an exception:
    never a partial list.

    Callers delete rows this poll did not see, so a silently missing page
    would read as those auctions having ended. Each ~1MB page is filtered as it
    arrives, so only a few hundred listings are ever held at once.
    """
    first_page = await _get_json(client, "auctions", {"page": 0})
    total_pages = first_page.get("totalPages", 1)
    listings = _seymour_listings(first_page)
    del first_page

    gate = asyncio.Semaphore(PAGE_CONCURRENCY)

    async def fetch(page: int) -> list[dict]:
        async with gate:
            return _seymour_listings(await _get_json(client, "auctions", {"page": page}))

    for page_listings in await asyncio.gather(*(fetch(p) for p in range(1, total_pages))):
        listings.extend(page_listings)

    return listings


async def get_auctions_ended(client: httpx.AsyncClient) -> dict:
    return await _get_json(client, "auctions_ended")


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
    if not is_seymour_listing(auction):
        return None
    item_bytes = auction.get("item_bytes")
    if not item_bytes:
        return None

    item_id, item_uuid, hex_color = extract_nbt_data(item_bytes)
    if item_id not in ITEM_ID_TO_PIECE or not hex_color:
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
        piece=ITEM_ID_TO_PIECE[item_id],
        bids=parse_bids(raw_bids) if not is_bin else None,
    )


def parse_ended_auction(auction: dict) -> SeymourEndedAuction | None:
    item_bytes = auction.get("item_bytes")
    if not item_bytes:
        return None
    
    item_id, item_uuid, hex_color = extract_nbt_data(item_bytes)
    if item_id not in ITEM_ID_TO_PIECE or not hex_color:
        return None

    return SeymourEndedAuction(
        auction_id=auction.get("auction_id", ""),
        seller=auction.get("seller", ""),
        buyer=auction.get("buyer", ""),
        price=auction.get("price", 0),
        is_bin=auction.get("bin", False),
        item_uuid=auction.get("item_uuid") or item_uuid or "",
        hex_color=hex_color,
        piece=ITEM_ID_TO_PIECE[item_id],
        timestamp=auction.get("timestamp", 0),
    )


async def get_active_seymour_auctions(client: httpx.AsyncClient) -> list[SeymourAuction]:
    auctions = await get_all_auctions(client)
    return [a for auction in auctions if (a := parse_active_auction(auction))]


async def get_ended_seymour_auctions(client: httpx.AsyncClient) -> list[SeymourEndedAuction]:
    data = await get_auctions_ended(client)
    auctions = data.get("auctions", [])
    return [a for auction in auctions if (a := parse_ended_auction(auction))]
