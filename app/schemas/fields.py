"""Constrained field types shared across schemas.

Hex colours are normalised to upper case on the way in so downstream code can
compare them directly. Rows written before this existed may still be lower
case, hence the defensive .upper() calls scattered through app/services.
"""
from typing import Annotated

from pydantic import Field, StringConstraints

HexColor = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9A-Fa-f]{6}$", to_upper=True),
]

# Ceiling is the signed 32-bit INT the price columns are stored in.
Price = Annotated[int, Field(gt=0, le=2**31 - 1)]
