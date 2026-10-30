from app.ingredient_matcher import IngredientCatalog, IngredientEntry
from scripts.reparse_recipe_ingredients import reparse_recipe_ingredients


def test_reparse_keeps_core_ingredient_and_quarantines_low_coverage() -> None:
    catalog = IngredientCatalog(
        [
            IngredientEntry("주꾸미", ("쭈꾸미",)),
            IngredientEntry("양파", ()),
            IngredientEntry("당근", ()),
        ]
    )
    recipes = [
        {
            "source": "MFDS_COOKRCP01",
            "source_recipe_id": "414",
            "recipe_name": "주꾸미머리순대",
            "source_ingredients_raw": "주꾸미 200g, 양파 25g, 당근 20g",
            "ingredients": [],
        },
        {
            "source": "MFDS_COOKRCP01",
            "source_recipe_id": "bad",
            "recipe_name": "알 수 없는 요리",
            "source_ingredients_raw": "미등록재료 100g, 양파 20g",
            "ingredients": [],
        },
    ]

    accepted, quarantined, statistics = reparse_recipe_ingredients(
        recipes,
        catalog=catalog,
        minimum_coverage=0.7,
    )

    assert [item["recipe_name"] for item in accepted] == ["주꾸미머리순대"]
    assert [
        item["canonical_name"] for item in accepted[0]["ingredients"]
    ] == ["주꾸미", "양파", "당근"]
    assert accepted[0]["ingredients"][0]["primary"] is True
    assert not any(
        item.get("primary") for item in accepted[0]["ingredients"][1:]
    )
    assert quarantined[0]["source_recipe_id"] == "bad"
    assert "LOW_PARSE_COVERAGE" in quarantined[0]["reasons"]
    assert statistics["quarantined_recipe_count"] == 1
