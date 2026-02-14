from datetime import datetime
from enum import Enum
from pydantic import BaseModel, ConfigDict


class ItemName(str, Enum):
    CASHMERE_JACKET = "Cashmere Jacket"
    OXFORD_SHOES = "Oxford Shoes"
    SATIN_TROUSERS = "Satin Trousers"
    VELVET_TOP_HAT = "Velvet Top Hat"


class SeymourBase(BaseModel):
    item_name: ItemName
    hex_color: str


class SeymourCreate(SeymourBase):
    pass


class SeymourResponse(SeymourBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UploadResponse(BaseModel):
    imported: int
    skipped: int

