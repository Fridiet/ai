"""Local-only, transactional ingredient taxonomy synchronization; dry-run by default.

Never rewrites inventory or user recipes. Existing canonical IDs are immutable.
Catalog IDs must already match the reviewed dataset; unrelated catalog rows remain.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path

from sqlalchemy import MetaData, select, inspect, text

from app.inventory_mapper import infer_ingredient_type, infer_default_unit
from app.receipt_mapper import infer_receipt_category
from scripts.seed_development_data import _resolve_engine

ROOT = Path(__file__).resolve().parents[1]
MUTABLE = {"ingredient_master", "ingredient_alias", "recipe_catalog_ingredients"}


def rows(conn, table):
    return [dict(row) for row in conn.execute(select(table)).mappings()]


def fingerprint(data):
    serialized = sorted(json.dumps(r, sort_keys=True, ensure_ascii=False, default=str) for r in data)
    return hashlib.sha256("\n".join(serialized).encode()).hexdigest()


def ingredient_key(row):
    quantity = row.get("quantity")
    # MySQL quantity is DECIMAL(10,2); compare using the persisted precision.
    return (row["canonical_name"], None if quantity is None else
            Decimal(str(quantity)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
            row.get("unit"), bool(row.get("optional", False)))


def alias_targets(entries):
    targets = {}
    # Include canonical names: an old alias must not shadow a new specific name.
    for entry in entries:
        for alias in [entry["name"], *entry.get("aliases", [])]:
            previous = targets.setdefault(alias, entry["name"])
            if previous != entry["name"]:
                raise ValueError(f"Ambiguous alias: {alias}: {previous}/{entry['name']}")
    return targets


def synchronize(conn, tables, entries, recipes, apply=False):
    counts = Counter()
    master, alias, catalog, links = (tables[n] for n in
        ("ingredient_master", "ingredient_alias", "recipe_catalog", "recipe_catalog_ingredients"))
    master_rows = rows(conn, master)
    ids = {r["canonical_name"]: r["ingredient_id"] for r in master_rows}
    if len(ids) != len(master_rows):
        raise ValueError("Duplicate canonical names in DB")
    targets = alias_targets(entries)
    for entry in entries:
        name = entry["name"]
        if name in ids:
            continue
        kind = infer_ingredient_type(name, None)
        values = dict(canonical_name=name, ingredient_type=kind,
                      default_unit=infer_default_unit(name, kind))
        if "category" in master.c:
            values["category"] = infer_receipt_category({
                "ingredient_master_candidate": values, "classification": "fresh_food"})
        counts["masters_inserted"] += 1
        ids[name] = (conn.execute(master.insert().values(**values)).inserted_primary_key[0]
                     if apply else -counts["masters_inserted"])
    aliases = {}
    for row in rows(conn, alias):
        aliases.setdefault(row["alias_text"], []).append(row)
    for label, name in targets.items():
        existing = aliases.get(label, [])
        if not existing:
            counts["aliases_inserted"] += 1
            if apply:
                conn.execute(alias.insert().values(alias_text=label, ingredient_id=ids[name], confidence=1.0))
        for row in existing:
            if row["ingredient_id"] != ids[name]:
                counts["aliases_remapped"] += 1
                if apply:
                    conn.execute(alias.update().where(alias.c.alias_id == row["alias_id"])
                                 .values(ingredient_id=ids[name], confidence=1.0))
    catalog_rows = rows(conn, catalog)
    by_source = {(r["source"], r["source_recipe_id"]): r for r in catalog_rows}
    if len(by_source) != len(catalog_rows):
        raise ValueError("Duplicate catalog source IDs")
    all_links = {}
    for row in rows(conn, links):
        all_links.setdefault(row["recipe_catalog_id"], []).append(row)
    for recipe in recipes:
        source_key = (recipe.get("source", "FRIDIET_CURATED_MVP"), recipe["source_recipe_id"])
        current = by_source.get(source_key)
        if current is None or current["recipe_name"] != recipe["recipe_name"]:
            raise ValueError(f"Catalog identity mismatch: {source_key}")
        recipe_id = current["recipe_catalog_id"]
        available = list(all_links.get(recipe_id, []))
        changed = False
        for ingredient in recipe["ingredients"]:
            name = ingredient["canonical_name"]
            if name not in ids:
                raise ValueError(f"Missing master: {name}")
            matching = next((r for r in available if ingredient_key(r) == ingredient_key(ingredient)
                             and r["ingredient_id"] == ids[name]), None)
            if matching is not None:
                available.remove(matching)
                continue
            changed = True
            same_master = next((r for r in available if r['ingredient_id'] == ids[name]), None)
            if same_master is not None:
                available.remove(same_master)
                counts['recipe_links_updated'] += 1
                if apply:
                    conn.execute(links.update().where(links.c.recipe_catalog_ingredient_id ==
                        same_master['recipe_catalog_ingredient_id']).values(canonical_name=name,
                        quantity=ingredient.get('quantity'), unit=ingredient.get('unit'),
                        optional=bool(ingredient.get('optional', False))))
                continue
            counts["recipe_links_inserted"] += 1
            if apply:
                conn.execute(links.insert().values(
                    recipe_catalog_id=recipe_id, ingredient_id=ids[name], canonical_name=name,
                    quantity=ingredient.get("quantity"), unit=ingredient.get("unit"),
                    optional=bool(ingredient.get("optional", False))))
        for old in available:
            changed = True
            counts["recipe_links_removed"] += 1
            if apply:
                conn.execute(links.delete().where(
                    links.c.recipe_catalog_ingredient_id == old["recipe_catalog_ingredient_id"]))
        counts["recipes_changed"] += int(changed)
    return dict(counts)


def run(apply=False):
    engine = _resolve_engine(None)
    if engine.url.host not in ("localhost", "127.0.0.1", "::1"):
        raise RuntimeError("Only local databases are allowed")
    inspector = inspect(engine)
    names = inspector.get_table_names()
    for name in names:
        for fk in inspector.get_foreign_keys(name):
            if fk["referred_table"] == "recipe_catalog_ingredients":
                raise RuntimeError(f"Catalog ingredient rows are referenced by {name}; manual migration required")
    metadata = MetaData()
    metadata.reflect(bind=engine)
    entries = json.loads((ROOT / "app/data/ingredients_ko.json").read_text(encoding="utf-8"))
    recipes = json.loads((ROOT / "app/data/recipes_ko.json").read_text(encoding="utf-8"))
    output = ROOT / "output/ingredient_specificity/database"
    output.mkdir(parents=True, exist_ok=True)
    with engine.begin() as conn:
        engines = conn.execute(text("SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
                                    "WHERE TABLE_SCHEMA=DATABASE()")).all()
        if any(storage != "InnoDB" for name, storage in engines if name in MUTABLE):
            raise RuntimeError("Transactional InnoDB tables required")
        before = {name: rows(conn, metadata.tables[name]) for name in names}
        protected = {name: fingerprint(data) for name, data in before.items() if name not in MUTABLE}
        report = {"database": engine.url.database, "apply": apply,
                  "dataset_recipes": len(recipes), "dictionary_entries": len(entries)}
        if apply:
            backup = output / (datetime.now().strftime("backup-%Y%m%d-%H%M%S-%f") + ".json")
            # Reference data only; no credentials, accounts, or behavioral history in backup.
            backup.write_text(json.dumps({name: before[name] for name in MUTABLE},
                                         ensure_ascii=False, default=str), encoding="utf-8")
            report["backup"] = str(backup)
        report["changes"] = synchronize(conn, metadata.tables, entries, recipes, apply)
        if apply:
            remaining = synchronize(conn, metadata.tables, entries, recipes, False)
            if any(remaining.values()):
                raise RuntimeError(f"Not idempotent; rolling back: {remaining}")
        for name, digest in protected.items():
            if fingerprint(rows(conn, metadata.tables[name])) != digest:
                raise RuntimeError(f"Protected table changed; rolling back: {name}")
        report["protected_tables_unchanged"] = len(protected)
        report["master_ids_preserved"] = all(
            conn.execute(select(metadata.tables['ingredient_master'].c.canonical_name).where(
                metadata.tables['ingredient_master'].c.ingredient_id == r['ingredient_id'])).scalar_one()
            == r['canonical_name'] for r in before['ingredient_master'])
        if not report["master_ids_preserved"]:
            raise RuntimeError("Master ID changed; rolling back")
    # Report is written only after transaction commits successfully.
    (output / ("applied.json" if apply else "dry-run.json")).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.apply), ensure_ascii=False, indent=2))
