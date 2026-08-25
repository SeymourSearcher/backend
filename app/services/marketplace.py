from app.clock import utcnow
from app.models import OrderStatus, SellOrder
from app.services.compute import delta_e, get_lab, SETS, ITEM_TO_INFO, find_matching_seymours, KEY_ITEM_HEXES, HEX_TO_ITEMS, ITEM_TO_SET
from app.services.matching import TIER_THRESHOLDS

def get_set_available_pieces(set_name: str) -> list[int]:
    available = set()
    for item_name in SETS.get(set_name, []):
        info = ITEM_TO_INFO.get(item_name)
        if info:
            available.update(info["pieces"])
    return sorted(available)


def get_all_sets_pieces() -> dict[str, list[int]]:
    return {set_name: get_set_available_pieces(set_name) for set_name in SETS}


def _compute_delta_e(user_hex: str, target_hex: str) -> float:
    return float(delta_e(get_lab(user_hex), get_lab(target_hex)))


def _find_piece_in_set(set_name: str, piece_type: int):
    for item_name in SETS.get(set_name, []):
        info = ITEM_TO_INFO.get(item_name)
        if info and piece_type in info["pieces"]:
            return info
    return None


def get_set_target_hex(set_name: str, piece_type: int) -> str | None:
    info = _find_piece_in_set(set_name, piece_type)
    return info["hex"] if info else None


def is_valid_set(set_name: str) -> bool:
    return set_name in SETS


def is_valid_piece_for_set(set_name: str, piece_type: int) -> bool:
    return _find_piece_in_set(set_name, piece_type) is not None


def matches_tier(user_hex: str, target_hex: str, tier: str) -> bool:
    de = _compute_delta_e(user_hex, target_hex)
    min_de, max_de = TIER_THRESHOLDS[tier]
    return min_de <= de < max_de


def matches_exact_hex(user_hex: str, target_hex: str) -> bool:
    return user_hex.upper() == target_hex.upper()


def matches_hex_pattern(user_hex: str, pattern: str) -> bool:
    user_hex, pattern = user_hex.upper(), pattern.upper()
    if len(pattern) != 6:
        return False
    
    hex_literals = set('0123456789ABCDEF')
    bindings = {}
    for uc, pc in zip(user_hex, pattern):
        if pc in hex_literals:
            if uc != pc:
                return False
        else:
            if pc in bindings:
                if bindings[pc] != uc:
                    return False
            else:
                bindings[pc] = uc
    
    vals = list(bindings.values())
    return len(vals) == len(set(vals))


def matches_delta_e(user_hex: str, target_hex: str, max_de: float) -> bool:
    return _compute_delta_e(user_hex, target_hex) <= max_de


def seymour_matches_set_order(
    user_hex: str,
    user_piece: int,
    set_name: str,
    order_pieces: list[int],
    tier: str
) -> bool:
    if user_piece not in order_pieces:
        return False
    target_hex = get_set_target_hex(set_name, user_piece)
    return matches_tier(user_hex, target_hex, tier) if target_hex else False


def seymour_matches_hex_order(
    user_hex: str,
    user_piece: int,
    match_type: str,
    hex_value: str,
    max_delta_e: float | None,
    order_piece: int | None
) -> bool:
    if order_piece is not None and user_piece != order_piece:
        return False
    
    match match_type:
        case "exact":
            return matches_exact_hex(user_hex, hex_value)
        case "pattern":
            return matches_hex_pattern(user_hex, hex_value)
        case "delta_e":
            return matches_delta_e(user_hex, hex_value, max_delta_e) if max_delta_e is not None else False
        case _:
            return False


def get_seymour_best_match(hex_color: str, piece_type: int) -> dict | None:
    seymour_list = [(hex_color, piece_type)]
    matches = find_matching_seymours(seymour_list, KEY_ITEM_HEXES, threshold=10.0, best_only=True)
    
    if not matches:
        return None
    
    match = matches[0]
    target_hex = match[1][0]
    de = match[2]
    
    item_names = HEX_TO_ITEMS.get(target_hex, [])
    if item_names:
        item_name = item_names[0]
        set_name = ITEM_TO_SET.get(item_name, item_name)
    else:
        set_name = f"#{target_hex}"
    
    return {
        "target_name": set_name,
        "target_hex": target_hex,
        "delta_e": round(de, 2)
    }


def is_expired(order) -> bool:
    """Expiry is derived at read time; a lapsed order must never be written to."""
    return order.status == OrderStatus.ACTIVE and order.expires_at < utcnow()


def with_derived_status(response, order):
    if is_expired(order):
        return response.model_copy(update={"status": OrderStatus.EXPIRED})
    return response


def build_sell_order_response(order: SellOrder) -> dict:
    seymour = order.seymour
    match = get_seymour_best_match(seymour.hex_color, seymour.item_name)

    return {
        "id": order.id,
        "price": order.price,
        "status": order.status,
        "created_at": order.created_at,
        "expires_at": order.expires_at,
        "seymour": {
            "id": seymour.id,
            "item_name": seymour.item_name,
            "hex_color": seymour.hex_color,
            "match": match,
        },
        "user": {
            "discord_id": str(order.user.discord_id),
            "username": order.user.username,
            "avatar_hash": order.user.avatar_hash,
        } if order.user else None,
    }
