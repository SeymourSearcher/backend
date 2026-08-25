import json
import re

from pydantic import ValidationError

from app.pieces import ITEM_ID_TO_PIECE, ITEM_NAME_TO_PIECE, Piece
from app.schemas.seymour import SeymourCreate

# Pattern for quoted format: "uuid" "Item Name" "HexColor" "Match Info"
QUOTED_LINE_PATTERN = re.compile(r'"([^"]+)"\s+"([^"]+)"\s+"([^"]+)"\s+"([^"]+)"')

# Pattern for markdown format: `VELVET_TOP_HAT`, hex: `0056E2` (**ORIGINAL**) - Location: ...
MARKDOWN_LINE_PATTERN = re.compile(r'`([A-Z_]+)`,\s*hex:\s*`([0-9A-Fa-f]{6})`')


def _build(item_name: Piece, hex_color: str) -> SeymourCreate | None:
    """None rather than raising: a malformed hex is a skipped line, not a failed upload."""
    try:
        return SeymourCreate(item_name=item_name, hex_color=hex_color)
    except ValidationError:
        return None


def parse_pipe_format_line(line: str) -> SeymourCreate | None:
    """Parse lines like: Velvet Top Hat | #15E9E8 | Top: Aurora - Stage 20 (ΔE: 5.00 | Abs: 55)"""
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 2:
        return None

    item_name_str = parts[0]
    hex_color = parts[1].lstrip("#")
    
    item_name = None
    for display_name, piece in ITEM_NAME_TO_PIECE.items():
        if item_name_str.endswith(display_name):
            item_name = piece
            break

    if item_name is None:
        return None

    return _build(item_name, hex_color)


def parse_quoted_format_line(line: str) -> SeymourCreate | None:
    """Parse lines like: "uuid" "Satin Trousers" "0A9256" "Fig Armor 3p 2.1" """
    match = QUOTED_LINE_PATTERN.match(line)
    if not match:
        return None
    
    # Groups: 1=uuid, 2=item_name, 3=hex_color, 4=match_info
    item_name_str = match.group(2)
    hex_color = match.group(3).lstrip("#")
    
    item_name = ITEM_NAME_TO_PIECE.get(item_name_str)
    if item_name is None:
        return None

    return _build(item_name, hex_color)


def parse_markdown_format_line(line: str) -> SeymourCreate | None:
    """Parse lines like: `VELVET_TOP_HAT`, hex: `0056E2` (**ORIGINAL**) - Location: ..."""
    match = MARKDOWN_LINE_PATTERN.search(line)
    if not match:
        return None
    
    item_name_enum = match.group(1)  # e.g., "VELVET_TOP_HAT"
    hex_color = match.group(2).lstrip("#")
    
    item_name = ITEM_ID_TO_PIECE.get(item_name_enum)
    if item_name is None:
        return None

    return _build(item_name, hex_color)


def parse_seymour_line(line: str) -> SeymourCreate | None:
    line = line.strip()
    if not line:
        return None

    # Try quoted format first (starts with ")
    if line.startswith('"'):
        return parse_quoted_format_line(line)
    
    # Try markdown format (contains backticks)
    if '`' in line:
        return parse_markdown_format_line(line)
    
    # Fall back to pipe format
    return parse_pipe_format_line(line)


def parse_json_format(content: str) -> tuple[list[SeymourCreate], int] | None:
    """Parse JSON format with UUID keys containing pieceName and hexcode."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    
    if not isinstance(data, dict):
        return None
    
    parsed = []
    skipped = 0
    
    for entry in data.values():
        if not isinstance(entry, dict):
            skipped += 1
            continue
        
        piece_name = entry.get("pieceName")
        hex_code = entry.get("hexcode", "").lstrip("#")
        
        if not piece_name or not hex_code:
            skipped += 1
            continue
        
        item_name = ITEM_NAME_TO_PIECE.get(piece_name)
        if item_name is None:
            skipped += 1
            continue

        seymour = _build(item_name, hex_code)
        if seymour is None:
            skipped += 1
            continue

        parsed.append(seymour)
    
    return parsed, skipped


def parse_seymour_file(content: str) -> tuple[list[SeymourCreate], int]:
    content = content.strip()
    
    if content.startswith("{"):
        result = parse_json_format(content)
        if result:
            return result
    
    lines = content.splitlines()
    parsed = []
    skipped = 0

    for line in lines:
        result = parse_seymour_line(line)
        if result:
            parsed.append(result)
        elif line.strip():
            skipped += 1

    return parsed, skipped
