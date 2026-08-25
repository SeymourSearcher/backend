"""Parse a Seymour export and print the stats the API would compute for it.

    python -m scripts.seymour_stats "path/to/export.txt"

Was an __main__ block in app/services/stats.py with a hardcoded personal path.
"""
import argparse
import json

from app.services.compute import seymour_converter
from app.services.parser import parse_seymour_file
from app.services.stats import get_stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Seymour export file (any supported format)")
    args = parser.parse_args()

    with open(args.path, encoding="utf-8") as f:
        parsed, skipped = parse_seymour_file(f.read())

    print(f"parsed={len(parsed)} skipped={skipped}")
    print(json.dumps(get_stats(seymour_converter(parsed)), indent=2))


if __name__ == "__main__":
    main()
