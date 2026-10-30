import json
from pathlib import Path

from scripts.sync_recipe_ingredient_dictionary import sync_dictionary


def test_sync_dictionary_adds_missing_core_and_reuses_canonical_meat() -> None:
    dictionary = [
        {"name": "우유", "aliases": []},
        {"name": "꿀", "aliases": []},
        {"name": "돼지고기", "aliases": ["삼겹살"]},
    ]
    recipes = [
        {
            "recipe_name": "자두 라떼",
            "source_ingredients_raw": "자두(200g), 우유(250g), 꿀(10g)",
        },
        {
            "recipe_name": "통삼겹 구이",
            "source_ingredients_raw": "통삼겹 300g",
        },
    ]

    updated, statistics = sync_dictionary(dictionary, recipes)
    by_name = {entry["name"]: entry for entry in updated}

    assert "자두" in by_name
    assert "통삼겹" in by_name["돼지고기"]["aliases"]
    assert "통삼겹" not in by_name
    assert statistics["recipe_count"] == 2
    assert statistics["output_dictionary_count"] > len(dictionary)
    assert statistics["unmapped_source_term_count"] == 0


def test_sync_dictionary_prefers_canonical_name_over_conflicting_alias() -> None:
    dictionary = [
        {"name": "북어", "aliases": []},
        {"name": "명태", "aliases": ["북어"]},
        {"name": "마늘", "aliases": ["다진마늘", "다진 마늘"]},
    ]

    updated, _ = sync_dictionary(dictionary, [])
    by_name = {entry["name"]: entry for entry in updated}

    assert "북어" not in by_name["명태"]["aliases"]
    assert "다진마늘" in by_name["마늘"]["aliases"]
    assert "다진 마늘" not in by_name["마늘"]["aliases"]


def test_checked_in_recipe_dataset_has_complete_unique_ingredient_mappings() -> None:
    data_dir = Path(__file__).parents[1] / "app" / "data"
    dictionary = json.loads(
        (data_dir / "ingredients_ko.json").read_text(encoding="utf-8")
    )
    recipes = json.loads((data_dir / "recipes_ko.json").read_text(encoding="utf-8"))

    updated, statistics = sync_dictionary(dictionary, recipes)
    plum_latte = next(
        recipe for recipe in recipes if recipe.get("source_recipe_id") == "1128"
    )

    assert len(recipes) >= 900
    assert updated == dictionary
    assert statistics["unmapped_source_term_count"] == 0
    assert all(recipe.get("ingredient_parse_coverage", 1.0) == 1.0 for recipe in recipes)
    assert all(
        len(names := [item["canonical_name"] for item in recipe["ingredients"]])
        == len(set(names))
        for recipe in recipes
    )
    assert plum_latte["ingredients"][0] == {
        "canonical_name": "자두",
        "quantity": 200,
        "unit": "g",
        "optional": False,
        "primary": True,
    }
