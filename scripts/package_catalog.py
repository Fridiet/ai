from __future__ import annotations

import argparse
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .bootstrap_data import BootstrapError, sha256, validate_database


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Package products.sqlite3 for a GitHub Release or object storage."
    )
    parser.add_argument(
        "database",
        nargs="?",
        type=Path,
        default=PROJECT_ROOT / "app" / "data" / "products.sqlite3",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "dist" / "products.sqlite3.zip",
    )
    args = parser.parse_args()

    try:
        count = validate_database(args.database)
    except BootstrapError as error:
        print(f"[package error] {error}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = args.output.with_suffix(f"{args.output.suffix}.tmp")
    if temporary_path.exists():
        temporary_path.unlink()
    with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        archive.write(args.database, arcname="products.sqlite3")
    temporary_path.replace(args.output)

    print(f"packaged: {count:,} products -> {args.output}")
    print(f"archive_sha256={sha256(args.output)}")
    print(f"database_sha256={sha256(args.database)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
