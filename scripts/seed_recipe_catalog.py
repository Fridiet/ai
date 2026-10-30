import json
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.database import get_engine


DATASET_PATH = Path(__file__).parents[1] / "app" / "data" / "recipes_ko.json"
DEFAULT_SOURCE = "FRIDIET_CURATED_MVP"


def seed_recipe_catalog(
    engine: Engine | None = None,
    dataset_path: Path = DATASET_PATH,
) -> dict[str, int]:
    target_engine = engine or get_engine()
    table_names = set(inspect(target_engine).get_table_names())
    required = {"recipe_catalog", "recipe_catalog_ingredients", "ingredient_master"}
    missing = required - table_names
    if missing:
        raise RuntimeError(f"레시피 적재 테이블이 없습니다: {', '.join(sorted(missing))}")
    recipe_columns = {column["name"] for column in inspect(target_engine).get_columns("recipe_catalog")}
    nutrition_columns = {
        "serving_size_g",
        "calories_kcal",
        "carbohydrate_g",
        "protein_g",
        "fat_g",
        "sodium_mg",
        "nutrition_source",
    }
    if not nutrition_columns.issubset(recipe_columns):
        raise RuntimeError("migrations/004_add_personalized_nutrition.sql을 먼저 적용하세요.")

    recipes = json.loads(dataset_path.read_text(encoding="utf-8"))
    inserted_recipe_count = 0
    updated_recipe_count = 0
    ingredient_mapping_count = 0
    nutrition_recipe_count = 0

    with Session(target_engine) as session, session.begin():
        for recipe in recipes:
            source = recipe.get("source", DEFAULT_SOURCE)
            recipe_id = session.execute(
                text(
                    "SELECT recipe_catalog_id FROM recipe_catalog "
                    "WHERE source=:source AND source_recipe_id=:source_recipe_id LIMIT 1"
                ),
                {"source": source, "source_recipe_id": recipe["source_recipe_id"]},
            ).scalar_one_or_none()
            nutrition = recipe.get("nutrition") or {}
            if nutrition:
                nutrition_recipe_count += 1
            values = {
                "source_recipe_id": recipe["source_recipe_id"],
                "recipe_name": recipe["recipe_name"],
                "cook_time": recipe.get("cook_time"),
                "category": recipe.get("category"),
                "description": recipe.get("description"),
                "steps_json": json.dumps(recipe["steps"], ensure_ascii=False),
                "image_url": recipe.get("image_url"),
                "source": source,
                "serving_size_g": nutrition.get("serving_size_g"),
                "calories_kcal": nutrition.get("calories_kcal"),
                "carbohydrate_g": nutrition.get("carbohydrate_g"),
                "protein_g": nutrition.get("protein_g"),
                "fat_g": nutrition.get("fat_g"),
                "sodium_mg": nutrition.get("sodium_mg"),
                "nutrition_source": nutrition.get("source"),
            }
            if recipe_id is None:
                result = session.execute(
                    text(
                        "INSERT INTO recipe_catalog "
                        "(source_recipe_id, recipe_name, cook_time, category, description, steps_json, "
                        "image_url, source, serving_size_g, calories_kcal, carbohydrate_g, protein_g, "
                        "fat_g, sodium_mg, nutrition_source) "
                        "VALUES (:source_recipe_id, :recipe_name, :cook_time, :category, :description, "
                        ":steps_json, :image_url, :source, :serving_size_g, :calories_kcal, "
                        ":carbohydrate_g, :protein_g, :fat_g, :sodium_mg, :nutrition_source)"
                    ),
                    values,
                )
                recipe_id = result.lastrowid
                inserted_recipe_count += 1
            else:
                session.execute(
                    text(
                        "UPDATE recipe_catalog SET recipe_name=:recipe_name, cook_time=:cook_time, "
                        "category=:category, description=:description, steps_json=:steps_json, "
                        "image_url=:image_url, serving_size_g=:serving_size_g, "
                        "calories_kcal=:calories_kcal, carbohydrate_g=:carbohydrate_g, "
                        "protein_g=:protein_g, fat_g=:fat_g, sodium_mg=:sodium_mg, "
                        "nutrition_source=:nutrition_source "
                        "WHERE recipe_catalog_id=:recipe_catalog_id"
                    ),
                    {**values, "recipe_catalog_id": recipe_id},
                )
                session.execute(
                    text(
                        "DELETE FROM recipe_catalog_ingredients "
                        "WHERE recipe_catalog_id=:recipe_catalog_id"
                    ),
                    {"recipe_catalog_id": recipe_id},
                )
                updated_recipe_count += 1

            for ingredient in recipe["ingredients"]:
                master = session.execute(
                    text(
                        "SELECT ingredient_id, canonical_name FROM ingredient_master "
                        "WHERE canonical_name=:canonical_name LIMIT 1"
                    ),
                    {"canonical_name": ingredient["canonical_name"]},
                ).mappings().one_or_none()
                if master is None:
                    raise RuntimeError(
                        f"ingredient_master에 없는 레시피 재료입니다: {ingredient['canonical_name']}"
                    )
                session.execute(
                    text(
                        "INSERT INTO recipe_catalog_ingredients "
                        "(recipe_catalog_id, ingredient_id, canonical_name, quantity, unit, optional) "
                        "VALUES (:recipe_catalog_id, :ingredient_id, :canonical_name, :quantity, :unit, :optional)"
                    ),
                    {
                        "recipe_catalog_id": recipe_id,
                        "ingredient_id": master["ingredient_id"],
                        "canonical_name": master["canonical_name"],
                        "quantity": ingredient.get("quantity"),
                        "unit": ingredient.get("unit"),
                        "optional": bool(ingredient.get("optional", False)),
                    },
                )
                ingredient_mapping_count += 1

    return {
        "dataset_recipe_count": len(recipes),
        "inserted_recipe_count": inserted_recipe_count,
        "updated_recipe_count": updated_recipe_count,
        "ingredient_mapping_count": ingredient_mapping_count,
        "nutrition_recipe_count": nutrition_recipe_count,
    }


def main() -> None:
    print(json.dumps(seed_recipe_catalog(), ensure_ascii=False))


if __name__ == "__main__":
    main()
