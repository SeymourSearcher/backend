"""Constrained field types shared across schemas.

Hex colours are normalised to upper case on the way in so downstream code can
compare them directly. Rows written before this existed may still be lower
case, hence the defensive .upper() calls scattered through app/services.
"""
from typing import Annotated

from pydantic import StringConstraints

HexColor = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9A-Fa-f]{6}$", to_upper=True),
]
