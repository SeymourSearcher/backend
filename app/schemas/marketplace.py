import re
from datetime import datetime
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from app.models.marketplace import OrderType, OrderStatus, HexMatchType, Tier
from app.pieces import Piece
from app.schemas.common import MatchBrief, PublicUser, SeymourBrief

HEX6 = re.compile(r"^[0-9A-F]{6}$")


class SetCriteriaCreate(BaseModel):
    set_name: str
    piece_types: list[Piece]
    tier: Tier

    @field_validator("piece_types")
    @classmethod
    def validate_piece_types(cls, v):
        if not v:
            raise ValueError("piece_types must not be empty")
        return sorted(set(v))


class HexCriteriaCreate(BaseModel):
    match_type: HexMatchType
    hex_value: str
    max_delta_e: float | None = None
    piece_type: Piece | None = None

    @model_validator(mode="after")
    def validate_hex_value(self):
        if len(self.hex_value) != 6:
            raise ValueError("hex_value must be 6 characters")

        self.hex_value = self.hex_value.upper()
        # PATTERN binds non-hex characters as variables; every other mode feeds
        # hex_value to int(v, 16), which raises on anything else.
        if self.match_type is not HexMatchType.PATTERN and not HEX6.match(self.hex_value):
            raise ValueError("hex_value must be a 6-digit hex colour")
        return self


class BuyOrderCreate(BaseModel):
    price: int
    order_type: OrderType
    set_criteria: SetCriteriaCreate | None = None
    hex_criteria: HexCriteriaCreate | None = None

    @field_validator("price")
    @classmethod
    def validate_price(cls, v):
        if v <= 0:
            raise ValueError("price must be positive")
        return v


class SetCriteriaResponse(BaseModel):
    set_name: str
    piece_types: list[Piece]
    tier: Tier

    model_config = ConfigDict(from_attributes=True)


class HexCriteriaResponse(BaseModel):
    match_type: HexMatchType
    hex_value: str
    max_delta_e: float | None
    piece_type: Piece | None

    model_config = ConfigDict(from_attributes=True)


class OrderUserResponse(PublicUser):
    pass


class BuyOrderResponse(BaseModel):
    id: int
    user_id: int
    price: int
    order_type: OrderType
    status: OrderStatus
    created_at: datetime
    expires_at: datetime
    set_criteria: SetCriteriaResponse | None = None
    hex_criteria: HexCriteriaResponse | None = None
    user: OrderUserResponse | None = None

    model_config = ConfigDict(from_attributes=True)


class BuyOrderListResponse(BaseModel):
    orders: list[BuyOrderResponse]
    total: int


class MatchingSeymourResponse(SeymourBrief):
    pass


class FillableOrderResponse(BaseModel):
    order: BuyOrderResponse
    matching_seymours: list[MatchingSeymourResponse]


class SellOrderCreate(BaseModel):
    seymour_ids: list[int]
    price: int
    expires_hours: int = 48

    @field_validator("seymour_ids")
    @classmethod
    def validate_seymour_ids(cls, v):
        if not v:
            raise ValueError("seymour_ids must not be empty")
        if len(v) > 50:
            raise ValueError("maximum 50 seymours per request")
        return list(set(v))

    @field_validator("price")
    @classmethod
    def validate_price(cls, v):
        if v <= 0:
            raise ValueError("price must be positive")
        return v

    @field_validator("expires_hours")
    @classmethod
    def validate_expires_hours(cls, v):
        if v < 1 or v > 168:
            raise ValueError("expires_hours must be between 1 and 168")
        return v


class SellOrderMatchResponse(MatchBrief):
    pass


class SellOrderSeymourResponse(SeymourBrief):
    match: SellOrderMatchResponse | None = None


class SellOrderResponse(BaseModel):
    id: int
    price: int
    status: OrderStatus
    created_at: datetime
    expires_at: datetime
    seymour: SellOrderSeymourResponse
    user: OrderUserResponse | None = None


class SellOrderListResponse(BaseModel):
    orders: list[SellOrderResponse]
    total: int
