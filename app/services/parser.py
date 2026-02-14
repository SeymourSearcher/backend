from app.schemas.seymour import SeymourCreate, ItemName, ITEM_DISPLAY_NAMES

DISPLAY_TO_ITEM = {v: k for k, v in ITEM_DISPLAY_NAMES.items()}


def parse_seymour_line(line: str) -> SeymourCreate | None:
    line = line.strip()
    if not line:
        return None

    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 2:
        return None

    item_name_str = parts[0]
    hex_color = parts[1][1:]
    
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


def parse_seymour_file(content: str) -> tuple[list[SeymourCreate], int]:
    lines = content.splitlines()
    parsed = []
    skipped = 0

    for line in lines:
        result = parse_seymour_line(line)
        if result:
            parsed.append(result)
        elif line.strip():
            print(line)
            skipped += 1

    return parsed, skipped


if __name__ == "__main__":
    with open(r"C:\Users\Hexay\Downloads\AllyMe's Export 2.9.2026.txt", 'r') as file:
        content = file.read()
        parsed, skipped = parse_seymour_file(content)
        print(f"Parsed: {len(parsed)}")
        print(f"Skipped: {skipped}")