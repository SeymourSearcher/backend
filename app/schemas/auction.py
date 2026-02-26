from pydantic import BaseModel, ConfigDict


class BidResponse(BaseModel):
    bidder_uuid: str
    amount: int
    timestamp: int

    model_config = ConfigDict(from_attributes=True)


class ActiveAuctionResponse(BaseModel):
    auction_id: str
    seller_uuid: str
    seller_name: str | None = None
    price: int
    is_bin: bool
    start_time: int
    end_time: int | None
    item_uuid: str
    hex_color: str
    piece: int
    tier: str | None = None
    delta_e: float = 0.0
    abs_diff: int = 0
    target_hex: str = ""

    model_config = ConfigDict(from_attributes=True)


class ActiveAuctionDetailResponse(ActiveAuctionResponse):
    bids: list[BidResponse] = []


class ActiveAuctionListResponse(BaseModel):
    auctions: list[ActiveAuctionResponse]
    total: int


class EndedAuctionResponse(BaseModel):
    auction_id: str
    seller_uuid: str
    seller_name: str | None = None
    buyer_uuid: str
    buyer_name: str | None = None
    price: int
    is_bin: bool
    item_uuid: str
    hex_color: str
    piece: int
    timestamp: int
    tier: str | None = None
    delta_e: float = 0.0
    abs_diff: int = 0
    target_hex: str = ""

    model_config = ConfigDict(from_attributes=True)


class EndedAuctionListResponse(BaseModel):
    auctions: list[EndedAuctionResponse]
    total: int
