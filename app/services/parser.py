import re
from app.schemas.seymour import SeymourCreate, ItemName, ITEM_DISPLAY_NAMES

DISPLAY_TO_ITEM = {v: k for k, v in ITEM_DISPLAY_NAMES.items()}

# Map from ENUM_STYLE names to ItemName enum values
ENUM_TO_ITEM = {item.name: item for item in ItemName}

# Pattern for quoted format: "uuid" "Item Name" "HexColor" "Match Info"
QUOTED_LINE_PATTERN = re.compile(r'"([^"]+)"\s+"([^"]+)"\s+"([^"]+)"\s+"([^"]+)"')

# Pattern for markdown format: `VELVET_TOP_HAT`, hex: `0056E2` (**ORIGINAL**) - Location: ...
MARKDOWN_LINE_PATTERN = re.compile(r'`([A-Z_]+)`,\s*hex:\s*`([0-9A-Fa-f]+)`')


def parse_pipe_format_line(line: str) -> SeymourCreate | None:
    """Parse lines like: Velvet Top Hat | #15E9E8 | Top: Aurora - Stage 20 (ΔE: 5.00 | Abs: 55)"""
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 2:
        return None

    item_name_str = parts[0]
    hex_color = parts[1].lstrip("#")
    
    item_name = None
    for display_name, enum_val in DISPLAY_TO_ITEM.items():
        if item_name_str.endswith(display_name):
            item_name = enum_val
            break
    
    if item_name is None:
        return None
    
    return SeymourCreate(
        item_name=item_name,
        hex_color=hex_color,
    )


def parse_quoted_format_line(line: str) -> SeymourCreate | None:
    """Parse lines like: "uuid" "Satin Trousers" "0A9256" "Fig Armor 3p 2.1" """
    match = QUOTED_LINE_PATTERN.match(line)
    if not match:
        return None
    
    # Groups: 1=uuid, 2=item_name, 3=hex_color, 4=match_info
    item_name_str = match.group(2)
    hex_color = match.group(3).lstrip("#")
    
    item_name = DISPLAY_TO_ITEM.get(item_name_str)
    if item_name is None:
        return None
    
    return SeymourCreate(
        item_name=item_name,
        hex_color=hex_color,
    )


def parse_markdown_format_line(line: str) -> SeymourCreate | None:
    """Parse lines like: `VELVET_TOP_HAT`, hex: `0056E2` (**ORIGINAL**) - Location: ..."""
    match = MARKDOWN_LINE_PATTERN.search(line)
    if not match:
        return None
    
    item_name_enum = match.group(1)  # e.g., "VELVET_TOP_HAT"
    hex_color = match.group(2).lstrip("#")
    
    item_name = ENUM_TO_ITEM.get(item_name_enum)
    if item_name is None:
        return None
    
    return SeymourCreate(
        item_name=item_name,
        hex_color=hex_color,
    )


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


def parse_seymour_file(content: str) -> tuple[list[SeymourCreate], int]:
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


if __name__ == "__main__":
    with open(r"C:\Users\Hexay\Downloads\AllyMe's Export 2.9.2026.txt", 'r') as file:
        content = file.read()
        parsed, skipped = parse_seymour_file(content)
        print(f"Parsed: {len(parsed)}")
        print(f"Skipped: {skipped}")