from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.ingredient_matcher import IngredientCatalog, get_ingredient_catalog
from scripts.import_mfds_recipes import (
    MIN_INGREDIENT_PARSE_COVERAGE,
    SOURCE_NAME,
    _has_missing_title_ingredient,
    _write_json_atomic,
    mark_primary_ingredients,
    parse_ingredients_with_audit,
)


AI_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECIPES_PATH = AI_ROOT / "app" / "data" / "recipes_ko.json"
DEFAULT_QUARANTINE_PATH = (
    AI_ROOT / "output" / "recipe_quality" / "quarantined_recipes.json"
)


def reparse_recipe_ingredients(
    recipes: list[dict[str, Any]],
    *,
    catalog: IngredientCatalog,
    minimum_coverage: float = MIN_INGREDIENT_PARSE_COVERAGE,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    source_recipe_count = 0
    total_segments = 0
    mapped_segments = 0
    ignored_segments = 0

    for recipe in recipes:
        raw_ingredients = str(recipe.get("source_ingredients_raw") or "").strip()
        if recipe.get("source") != SOURCE_NAME or not raw_ingredients:
            accepted.append(recipe)
            continue

        source_recipe_count += 1
        ingredients, audit = parse_ingredients_with_audit(raw_ingredients, catalog)
        total_segments += int(audit["quantified_segment_count"])
        mapped_segments += int(audit["mapped_segment_count"])
        ignored_segments += int(audit["ignored_segment_count"])

        reasons: list[str] = []
        if not ingredients:
            reasons.append("NO_MAPPED_INGREDIENT")
        elif not any(not ingredient["optional"] for ingredient in ingredients):
            reasons.append("NO_REQUIRED_INGREDIENT")
        if float(audit["coverage"]) < minimum_coverage:
            reasons.append("LOW_PARSE_COVERAGE")
        if _has_missing_title_ingredient(
            str(recipe.get("recipe_name") or ""),
            raw_ingredients,
            ingredients,
            catalog,
        ):
            reasons.append("MISSING_TITLE_INGREDIENT")

        if reasons:
            quarantined.append(
                {
                    "source_recipe_id": recipe.get("source_recipe_id"),
                    "recipe_name": recipe.get("recipe_name"),
                    "reasons": reasons,
                    "ingredient_parse_audit": audit,
                    "source_ingredients_raw": raw_ingredients,
                }
            )
            continue

        updated = dict(recipe)
        updated["ingredients"] = mark_primary_ingredients(
            str(recipe.get("recipe_name") or ""),
            ingredients,
            catalog,
        )
        updated["ingredient_parse_coverage"] = audit["coverage"]
        accepted.append(updated)

    accepted_segment_count = mapped_segments + ignored_segments
    return accepted, quarantined, {
        "input_recipe_count": len(recipes),
        "source_recipe_count": source_recipe_count,
        "accepted_recipe_count": len(accepted),
        "quarantined_recipe_count": len(quarantined),
        "quantified_segment_count": total_segments,
        "mapped_segment_count": mapped_segments,
        "ignored_segment_count": ignored_segments,
        "unmatched_segment_count": total_segments - accepted_segment_count,
        "segment_parse_coverage": round(
            accepted_segment_count / total_segments if total_segments else 0.0,
            4,
        ),
        "minimum_recipe_coverage": minimum_coverage,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="식약처 원본 문자열로 레시피 재료를 재파싱하고 저품질 행을 격리"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_RECIPES_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_RECIPES_PATH)
    parser.add_argument("--quarantine", type=Path, default=DEFAULT_QUARANTINE_PATH)
    parser.add_argument(
        "--minimum-coverage",
        type=float,
        default=MIN_INGREDIENT_PARSE_COVERAGE,
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 0 <= args.minimum_coverage <= 1:
        raise SystemExit("--minimum-coverage는 0~1 사이여야 합니다.")
    recipes = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(recipes, list):
        raise SystemExit("레시피 입력 파일은 JSON 배열이어야 합니다.")

    accepted, quarantined, statistics = reparse_recipe_ingredients(
        recipes,
        catalog=get_ingredient_catalog(),
        minimum_coverage=args.minimum_coverage,
    )
    if not args.dry_run:
        _write_json_atomic(args.output, accepted)
        args.quarantine.parent.mkdir(parents=True, exist_ok=True)
        args.quarantine.write_text(
            json.dumps(quarantined, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(statistics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
