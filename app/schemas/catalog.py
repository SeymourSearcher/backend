from pydantic import BaseModel


class CatalogTarget(BaseModel):
    hex: str
    name: str
