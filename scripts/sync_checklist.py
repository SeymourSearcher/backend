"""Rebuild app/static/data.json from the SeymourAnalyzer mod's checklist.

The mod is the maintained catalogue of Seymour target colours; we mirror its
names, hexes, categories and page order rather than curating our own. See
`backend/README.md`.

    python -m scripts.sync_checklist            # rewrite data.json
    python -m scripts.sync_checklist --check    # exit 1 if it would change

`sets` has no upstream counterpart -- it is our own grouping of items into
buyable armour sets, so it is carried over and remapped through
data_item_renames.json rather than regenerated. Items upstream added that no
set covers are reported, not invented; add them to `sets` by hand.

After running, the .npy artifacts and the auction match columns are stale, and
any renamed set is orphaned on live buy orders:

    python -m scripts.precompute_hex_matches
    python -m scripts.precompute_hex_order
    python -m scripts.migrate_buy_order_sets
    python -m scripts.backfill_auction_matches --all
"""
import argparse
import json
import os
import sys
from collections import defaultdict

import httpx

# Pinned so a rerun is reproducible; bump deliberately when re-syncing.
UPSTREAM_REF = "8ce4a0eee75288dc6604263f462c88b60b493346"
UPSTREAM_BASE = (
    f"https://raw.githubusercontent.com/Schnerry/SeymourAnalyzerFabric/{UPSTREAM_REF}"
    "/src/main/resources/data/seymouranalyzer"
)

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_JSON = os.path.join(SCRIPTS_DIR, "..", "app", "static", "data.json")
RENAMES_JSON = os.path.join(SCRIPTS_DIR, "data_item_renames.json")

PIECE_SUFFIX = {"helmet": "Helmet", "chestplate": "Chestplate", "leggings": "Leggings", "boots": "Boots"}


def _fetch(name: str) -> dict:
    response = httpx.get(f"{UPSTREAM_BASE}/{name}", timeout=30.0)
    response.raise_for_status()
    return response.json()


def _disambiguate(categories: dict) -> dict:
    """Suffix names that upstream reuses across *different* hexes.

    compute.ITEM_TO_INFO is keyed by name, so "Fairy Armor" appearing once per
    piece at four different hexes would silently collapse to whichever row
    loaded last. Same-hex repeats (Pure White in two categories) are harmless
    and left alone.
    """
    hexes_per_name = defaultdict(set)
    for items in categories.values():
        for item in items:
            hexes_per_name[item["name"]].add(item["hex"].upper())

    out = {}
    for category, items in categories.items():
        rows = []
        for item in items:
            name = item["name"]
            if len(hexes_per_name[name]) > 1:
                name = f"{name} ({'/'.join(PIECE_SUFFIX[p] for p in item['pieces'])})"
            rows.append({"hex": item["hex"].upper(), "name": name, "pieces": item["pieces"]})
        out[category] = rows
    return out


def _remap_sets(sets: dict, renames: dict, known_items: set) -> tuple[dict, dict]:
    """Carry `sets` across the rename, dropping members upstream no longer has.

    A set key that is itself an item name follows that item's rename, so the
    two stay in step. Distinct sets can collapse onto one key (Miners and
    Prospecting both became Miners/Prospecting Armor); their members merge.
    """
    remapped: dict[str, list] = {}
    dropped_members, dropped_sets, renamed_sets = [], [], {}

    for set_name, members in sets.items():
        new_members = []
        for member in members:
            new = renames.get(member, member)
            if new is None or new not in known_items:
                dropped_members.append(member)
            elif new not in new_members:
                new_members.append(new)
        if not new_members:
            dropped_sets.append(set_name)
            continue

        key = renames.get(set_name, set_name) or set_name
        if key != set_name:
            renamed_sets[set_name] = key
        for member in new_members:
            if member not in remapped.setdefault(key, []):
                remapped[key].append(member)

    return remapped, {
        "dropped_members": dropped_members,
        "dropped_sets": dropped_sets,
        "renamed_sets": renamed_sets,
    }


def build() -> tuple[dict, dict]:
    checklist = _fetch("checklistdata.json")
    colors = _fetch("colors.json")
    with open(DATA_JSON) as f:
        current = json.load(f)
    with open(RENAMES_JSON) as f:
        renames = json.load(f)

    categories = _disambiguate(checklist["categories"])
    ordered = {name: categories[name] for name in checklist["normalPageOrder"] if name in categories}
    ordered.update({name: items for name, items in categories.items() if name not in ordered})

    known_items = {item["name"] for items in ordered.values() for item in items}
    sets, report = _remap_sets(current["sets"], renames, known_items)

    data = {
        "sets": sets,
        "categories": ordered,
        "FADE_DYES": colors["FADE_DYES"],
        "HEX_WORDS": current["HEX_WORDS"],
    }
    # Only items upstream has newly introduced are worth flagging; the standing
    # set-less population (dyes, Fairy/Crystal/Gray stages) is deliberate.
    assigned = {member for members in sets.values() for member in members}
    previous = {renames.get(item["name"], item["name"])
                for items in current["categories"].values() for item in items}
    report["new_items_in_no_set"] = sorted(known_items - assigned - previous)
    return data, report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="exit 1 if data.json is out of date")
    args = parser.parse_args()

    data, report = build()
    serialized = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    with open(DATA_JSON) as f:
        unchanged = f.read() == serialized
    if args.check:
        print("up to date" if unchanged else "STALE: rerun without --check")
        return 0 if unchanged else 1

    with open(DATA_JSON, "w", encoding="utf-8") as f:
        f.write(serialized)

    items = sum(len(v) for v in data["categories"].values())
    print(f"wrote {DATA_JSON}")
    print(f"  {len(data['categories'])} categories, {items} items, "
          f"{len(data['FADE_DYES'])} fade stages, {len(data['sets'])} sets")
    for label, key in [("set members dropped", "dropped_members"), ("sets dropped", "dropped_sets"),
                       ("new items in no set", "new_items_in_no_set")]:
        if report[key]:
            print(f"  {label} ({len(report[key])}): {', '.join(report[key])}")
    if report["renamed_sets"]:
        print(f"  sets renamed ({len(report['renamed_sets'])}) -- "
              f"buy_order_set_criteria.set_name needs the same rename:")
        for old, new in report["renamed_sets"].items():
            print(f"    {old!r} -> {new!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
