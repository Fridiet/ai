import argparse
import re
import sqlite3
import unicodedata
from pathlib import Path
from xml.etree.ElementTree import iterparse
from zipfile import ZipFile


SELECTED_COLUMNS = {0, 1, 7, 9, 11, 13, 15, 155}
INVALID_CATEGORIES = {"", "해당없음", "None", "null"}


def product_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower().lstrip("\ufeff")
    return re.sub(r"[^0-9a-z가-힣]", "", normalized)


def column_index(reference: str) -> int:
    letters = "".join(character for character in reference if character.isalpha())
    value = 0
    for character in letters.upper():
        value = value * 26 + ord(character) - ord("A") + 1
    return value - 1


def load_shared_strings(archive: ZipFile) -> list[str]:
    strings: list[str] = []
    with archive.open("xl/sharedStrings.xml") as source:
        for _, element in iterparse(source, events=("end",)):
            if element.tag.endswith("}si"):
                text = "".join(node.text or "" for node in element.iter() if node.tag.endswith("}t"))
                strings.append(text)
                element.clear()
    return strings


def iter_selected_rows(xlsx_path: Path):
    with ZipFile(xlsx_path) as archive:
        shared_strings = load_shared_strings(archive)
        current: dict[int, str] = {}
        with archive.open("xl/worksheets/sheet1.xml") as source:
            for _, element in iterparse(source, events=("end",)):
                if element.tag.endswith("}c"):
                    index = column_index(element.attrib.get("r", "A1"))
                    if index in SELECTED_COLUMNS:
                        value_node = next((node for node in element if node.tag.endswith("}v")), None)
                        value = value_node.text if value_node is not None else ""
                        if element.attrib.get("t") == "s" and value:
                            value = shared_strings[int(value)]
                        current[index] = value or ""
                    element.clear()
                elif element.tag.endswith("}row"):
                    row_number = int(element.attrib.get("r", "0"))
                    if row_number > 1:
                        yield current
                    current = {}
                    element.clear()


def choose_category(row: dict[int, str]) -> tuple[str, str]:
    food_type = next(
        (row.get(index, "").strip() for index in (15, 13, 11, 9, 7) if row.get(index, "").strip() not in INVALID_CATEGORIES),
        "가공식품",
    )
    representative = row.get(9, "").strip()
    if food_type in {"유탕면", "건면"}:
        standard_name = "라면"
    elif representative not in INVALID_CATEGORIES:
        standard_name = representative
    else:
        standard_name = food_type
    return food_type, standard_name


def build_index(xlsx_path: Path, database_path: Path, source_name: str) -> int:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = database_path.with_name(f"{database_path.name}.tmp")
    if temporary_path.exists():
        temporary_path.unlink()
    connection = sqlite3.connect(temporary_path)
    connection.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        PRAGMA temp_store=MEMORY;
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            food_code TEXT,
            name TEXT NOT NULL,
            name_key TEXT NOT NULL,
            manufacturer TEXT,
            food_type TEXT NOT NULL,
            standard_food_name TEXT NOT NULL,
            source TEXT NOT NULL
        );
        CREATE INDEX idx_products_name_key ON products(name_key);
        """
    )

    batch: list[tuple[str, str, str, str, str, str, str]] = []
    count = 0
    for row in iter_selected_rows(xlsx_path):
        name = row.get(1, "").lstrip("\ufeff").strip()
        if not name:
            continue
        food_type, standard_name = choose_category(row)
        batch.append(
            (
                row.get(0, ""),
                name,
                product_key(name),
                row.get(155, "").strip(),
                food_type,
                standard_name,
                source_name,
            )
        )
        if len(batch) >= 5000:
            connection.executemany(
                "INSERT INTO products(food_code, name, name_key, manufacturer, food_type, standard_food_name, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            connection.commit()
            count += len(batch)
            batch.clear()
            if count % 25000 == 0:
                print(f"imported {count:,} products", flush=True)
    if batch:
        connection.executemany(
            "INSERT INTO products(food_code, name, name_key, manufacturer, food_type, standard_food_name, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
            batch,
        )
        count += len(batch)

    connection.executescript(
        """
        CREATE VIRTUAL TABLE product_search USING fts5(
            name_key,
            content='products',
            content_rowid='id',
            tokenize='trigram'
        );
        INSERT INTO product_search(product_search) VALUES('rebuild');
        PRAGMA optimize;
        """
    )
    connection.commit()
    connection.close()
    temporary_path.replace(database_path)
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the K-FIND processed-food XLSX into SQLite.")
    parser.add_argument("xlsx", type=Path)
    parser.add_argument("--output", type=Path, default=Path("app/data/products.sqlite3"))
    parser.add_argument("--source", default="K-FIND:2026-06-04")
    args = parser.parse_args()
    count = build_index(args.xlsx, args.output, args.source)
    print(f"completed: {count:,} products -> {args.output}")


if __name__ == "__main__":
    main()
