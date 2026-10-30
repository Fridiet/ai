"""Backfill unambiguous catalog identities in local legacy rows; never rename/delete rows.

Default is dry-run. After Spring has added nullable columns:
python -m scripts.backfill_recipe_keys --apply
Ambiguous names stay NULL rather than attributing behavior to the wrong recipe.
"""
import argparse
import json
from collections import defaultdict

from sqlalchemy import text

from app.team_recipe import _load_recipes
from scripts.seed_development_data import _resolve_engine


def backfill(apply=False):
    engine = _resolve_engine(None)
    if engine.url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("로컬 DB에서만 실행할 수 있습니다.")
    names = defaultdict(set)
    for recipe in _load_recipes():
        names[recipe["recipe_name"].strip()].add(f"{recipe.get('source') or 'LOCAL'}:{recipe['source_recipe_id']}")
    counts = {}
    with engine.begin() as db:
        for table, pk, extra in (
            ("recipe", "recipe_id", " AND is_generated=1"),
            ("recipe_recommendation_feedback", "feedback_id", ""),
        ):
            rows = db.execute(text(f"SELECT {pk}, recipe_name FROM {table} WHERE recipe_key IS NULL{extra}")).mappings()
            matched = skipped = 0
            for row in rows:
                keys = names[row["recipe_name"].strip()]
                if len(keys) != 1:
                    skipped += 1
                    continue
                matched += 1
                if apply:
                    db.execute(text(f"UPDATE {table} SET recipe_key=:key WHERE {pk}=:id AND recipe_key IS NULL"),
                               {"key": next(iter(keys)), "id": row[pk]})
            counts[table] = {"matched": matched, "ambiguous_or_unknown": skipped}
    return {"applied": apply, "tables": counts}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    print(json.dumps(backfill(parser.parse_args().apply), ensure_ascii=False, indent=2))
