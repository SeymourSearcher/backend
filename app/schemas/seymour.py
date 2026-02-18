from enum import IntEnum
from pydantic import BaseModel, ConfigDict, field_serializer


class ItemName(IntEnum):
    VELVET_TOP_HAT = 0
    CASHMERE_JACKET = 1
    SATIN_TROUSERS = 2
    OXFORD_SHOES = 3
    


ITEM_DISPLAY_NAMES = {
    ItemName.VELVET_TOP_HAT: "Velvet Top Hat",
    ItemName.CASHMERE_JACKET: "Cashmere Jacket",
    ItemName.SATIN_TROUSERS: "Satin Trousers",
    ItemName.OXFORD_SHOES: "Oxford Shoes",
    
}


class SeymourBase(BaseModel):
    item_name: ItemName
    hex_color: str

    @field_serializer("item_name")
    def serialize_item_name(self, v: ItemName) -> str:
        return ITEM_DISPLAY_NAMES[v]


class SeymourCreate(SeymourBase):
    pass


class SeymourResponse(SeymourBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


class MatchInfo(BaseModel):
    target_name: str
    target_hex: str
    delta_e: float
    missing: list[str] | None = None


class SeymourWithMatchResponse(BaseModel):
    id: int
    item_name: str
    hex_color: str
    match: MatchInfo | None = None


class UploadResponse(BaseModel):
    imported: int
    skipped: int

