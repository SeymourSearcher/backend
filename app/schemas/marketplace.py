from datetime import datetime
from pydantic import BaseModel, ConfigDict, field_validator
from app.models.marketplace import OrderType, OrderStatus, HexMatchType, Tier


class SetCriteriaCreate(BaseModel):
    set_name: str
    piece_types: list[int]
    tier: Tier

    @field_validator("piece_types")
    @classmethod
    def validate_piece_types(cls, v):
        if not v:
            raise ValueError("piece_types must not be empty")
        for pt in v:
            if pt < 0 or pt > 3:
                raise ValueError("piece_type values must be 0-3")
        return sorted(set(v))


class HexCriteriaCreate(BaseModel):
    match_type: HexMatchType
    hex_value: str
    max_delta_e: float | None = None
    piece_type: int | None = None

    @field_validator("hex_value")
    @classmethod
    def validate_hex_value(cls, v):
        if len(v) != 6:
            raise ValueError("hex_value must be 6 characters")
        return v.upper()

    @field_validator("piece_type")
    @classmethod
    def validate_piece_type(cls, v):
        if v is not None and (v < 0 or v > 3):
            raise ValueError("piece_type must be 0-3")
        return v


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
    piece_types: list[int]
    tier: Tier

    model_config = ConfigDict(from_attributes=True)


class HexCriteriaResponse(BaseModel):
    match_type: HexMatchType
    hex_value: str
    max_delta_e: float | None
    piece_type: int | None

    model_config = ConfigDict(from_attributes=True)


class OrderUserResponse(BaseModel):
    discord_id: str
    username: str
    avatar_hash: str | None = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("discord_id", mode="before")
    @classmethod
    def coerce_discord_id(cls, v):
        return str(v)


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


class MatchingSeymourResponse(BaseModel):
    id: int
    item_name: int
    hex_color: str

    model_config = ConfigDict(from_attributes=True)


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


class SellOrderMatchResponse(BaseModel):
    target_name: str
    target_hex: str
    delta_e: float


class SellOrderSeymourResponse(BaseModel):
    id: int
    item_name: int
    hex_color: str
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
