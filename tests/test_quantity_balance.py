import pytest
from app.quantity_balance import ingredient_balances
from app.team_recipe import _inventory_by_name
from datetime import date


@pytest.mark.parametrize("batches,status,shortage", [
    ([{"remaining_quantity":.2,"unit":"kg"}], "SHORTAGE",300),
    ([{"remaining_quantity":100,"unit":"g"},{"remaining_quantity":.2,"unit":"kg"}], "SHORTAGE",200),
    ([{"remaining_quantity":1,"unit":"개"}], "UNKNOWN",None),
    ([{"remaining_quantity":None,"unit":"g"}], "UNKNOWN",None),
    ([{"remaining_quantity":600,"unit":"g"},{"remaining_quantity":None,"unit":"g"}], "ENOUGH",0),
    ([], "SHORTAGE",500),
])
def test_conservative_balance(batches,status,shortage):
    result = ingredient_balances([{"canonical_name":"당근","quantity":500,"unit":"g"}], {"당근":batches})[0]
    assert (result["status"],result["shortageQuantity"]) == (status,shortage)


def test_duplicate_recipe_lines_do_not_reuse_stock():
    ingredients = [{"canonical_name":"당근","quantity":200,"unit":"g"}, {"canonical_name":"당근","quantity":.3,"unit":"kg"}]
    result = ingredient_balances(ingredients, {"당근":[{"remaining_quantity":400,"unit":"g"}]})
    assert len(result) == 1 and result[0]["shortageQuantity"] == 100


def test_expired_or_zero_stock_is_not_restored_from_legacy_names():
    result = _inventory_by_name([
        {"ingredientName":"당근","consumeByDate":"2026-01-01","remainingQuantity":500,"unit":"g"},
        {"ingredientName":"양파","remainingQuantity":0,"unit":"개"},
    ], ["당근","양파"], [], date(2026,9,11))
    assert result == {}


def test_shopping_includes_partial_stock_even_when_all_names_present(monkeypatch):
    from app.team_recipe import generate_team_recipe
    monkeypatch.setattr("app.team_recipe.get_two_tower_recommender", lambda: None)
    monkeypatch.setattr("app.team_recipe._load_recipes", lambda: [{"source_recipe_id":"test", "recipe_name":"당근 요리",
        "ingredients":[{"canonical_name":"당근","quantity":500,"unit":"g"}], "steps":["익힌다."]}])
    result = generate_team_recipe(health_condition=None,health_goal=None,active_inventories=[],
        additional_ingredients=[],exclude_ingredients=[],shopping_mode=True,
        inventories=[{"ingredientName":"당근","remainingQuantity":.2,"unit":"kg"}])
    assert result["missingIngredients"] == []
    assert result["ingredientBalances"][0]["shortageQuantity"] == 300
