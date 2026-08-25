"""The four Seymour armour pieces, and every name any layer calls them by.

A piece is identified on the wire and in the database by its index (0-3). The
three name vocabularies below are lookup tables only -- nothing else in the
backend may define its own.
"""
from enum import IntEnum


class Piece(IntEnum):
    HELMET = 0
    CHESTPLATE = 1
    LEGGINGS = 2
    BOOTS = 3


SLOT_NAMES = ["helmet", "chestplate", "leggings", "boots"]
ITEM_NAMES = ["Velvet Top Hat", "Cashmere Jacket", "Satin Trousers", "Oxford Shoes"]
ITEM_IDS = ["VELVET_TOP_HAT", "CASHMERE_JACKET", "SATIN_TROUSERS", "OXFORD_SHOES"]

SLOT_TO_PIECE = {name: Piece(i) for i, name in enumerate(SLOT_NAMES)}
ITEM_NAME_TO_PIECE = {name: Piece(i) for i, name in enumerate(ITEM_NAMES)}
ITEM_ID_TO_PIECE = {name: Piece(i) for i, name in enumerate(ITEM_IDS)}
