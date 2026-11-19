from app.services.compute import HEX_TO_ITEMS


def target_colours() -> list[dict]:
    """One row per distinct catalog hex, named after its leading item."""
    return [{"hex": hex_code, "name": names[0]} for hex_code, names in HEX_TO_ITEMS.items()]
