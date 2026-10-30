from datetime import date, datetime, timedelta

from app.team_recipe import generate_team_recipe


def test_shopping_recommendation_requires_missing_and_available_ingredients():
    result = generate_team_recipe(
        health_condition=None, health_goal=None,
        active_inventories=["당근", "양파"], additional_ingredients=[],
        exclude_ingredients=[], shopping_mode=True,
    )
    assert 1 <= len(result["missingIngredients"]) <= 3
    assert result["availableIngredients"]
    assert not set(result["missingIngredients"]) & set(result["availableIngredients"])


def test_shopping_recommendation_does_not_invent_stock():
    import pytest
    from app.team_recipe import TeamRecipeNotFoundError
    with pytest.raises(TeamRecipeNotFoundError):
        generate_team_recipe(health_condition=None, health_goal=None,
            active_inventories=[], additional_ingredients=[], exclude_ingredients=[], shopping_mode=True)


def test_generate_team_recipe_uses_spring_client_variables() -> None:
    result = generate_team_recipe(
        health_condition="당뇨",
        health_goal="혈당 관리",
        active_inventories=["당근"],
        additional_ingredients=["양파"],
        exclude_ingredients=["설탕"],
        target_ingredients=["당근", "양파"],
    )

    ingredient_names = {item["nameText"] for item in result["ingredients"]}
    assert {"당근", "양파"}.issubset(ingredient_names)
    assert "설탕" not in ingredient_names
    assert {"당근", "양파"}.issubset(set(result["availableIngredients"]))
    assert result["scoreBreakdown"]["nutrition_fit_score"] is not None
    assert result["cookingTime"] > 0
    assert result["steps"][0]["stepNumber"] == 1


def test_generate_team_recipe_excludes_requested_ingredient() -> None:
    result = generate_team_recipe(
        health_condition=None,
        health_goal=None,
        active_inventories=["당근", "양파"],
        additional_ingredients=[],
        exclude_ingredients=["식용유"],
    )

    assert all(
        ingredient["nameText"] != "식용유" for ingredient in result["ingredients"]
    )


def test_generate_team_recipe_requires_target_ingredient() -> None:
    result = generate_team_recipe(
        health_condition=None,
        health_goal=None,
        active_inventories=["고등어", "소고기", "닭고기", "무"],
        target_ingredients=["고등어"],
        additional_ingredients=[],
        exclude_ingredients=[],
    )

    assert result["title"] == "고등어 무조림"
    assert "고등어" in [item["nameText"] for item in result["ingredients"]]


def test_generate_team_recipe_prioritizes_expiring_inventory() -> None:
    today = date(2026, 8, 20)
    result = generate_team_recipe(
        health_condition=None,
        health_goal=None,
        active_inventories=["두부", "달걀", "우유"],
        inventories=[
            {
                "ingredientName": "두부",
                "consumeByDate": today,
                "remainingQuantity": 300,
                "unit": "g",
            },
            {
                "ingredientName": "달걀",
                "consumeByDate": today + timedelta(days=14),
                "remainingQuantity": 3,
                "unit": "개",
            },
            {
                "ingredientName": "우유",
                "consumeByDate": today + timedelta(days=14),
                "remainingQuantity": 100,
                "unit": "ml",
            },
        ],
        additional_ingredients=[],
        exclude_ingredients=[],
        today=today,
    )

    assert "두부" in result["availableIngredients"]
    assert result["missingIngredients"] == []
    assert result["scoreBreakdown"]["expiry_urgency"] > 0
    assert result["scoreBreakdown"]["two_tower_score"] > 0
    assert result["expiringIngredients"][0]["canonical_name"] == "두부"
    assert result["reasons"][0] == "두부의 소비기한이 오늘까지입니다."


def test_generate_team_recipe_applies_recent_cooked_diversity_score() -> None:
    now = datetime(2026, 8, 20, 12, 0)
    result = generate_team_recipe(
        health_condition=None,
        health_goal=None,
        active_inventories=["고등어", "무"],
        target_ingredients=["고등어"],
        additional_ingredients=[],
        exclude_ingredients=[],
        recent_feedback=[
            {
                "recipeName": "고등어 무조림",
                "eventType": "COOKED",
                "occurredAt": now - timedelta(days=2),
            }
        ],
        now=now,
    )

    assert result["title"] == "고등어 무조림"
    assert result["scoreBreakdown"]["menu_diversity"] == 0.0
    assert any(reason in {"최근 메뉴 반복을 고려했어요.", "저장·조리 이력을 반영했어요."}
               for reason in result["reasons"])


def test_generate_team_recipe_excludes_recently_dismissed_recipe() -> None:
    now = datetime(2026, 8, 20, 12, 0)
    result = generate_team_recipe(
        health_condition=None,
        health_goal=None,
        active_inventories=["당근", "양파", "달걀"],
        additional_ingredients=[],
        exclude_ingredients=[],
        recent_feedback=[
            {
                "recipeName": "당근 양파볶음",
                "eventType": "DISMISSED",
                "occurredAt": now,
            }
        ],
        now=now,
    )

    assert result["title"] != "당근 양파볶음"


def test_generate_team_recipe_requires_primary_ingredient() -> None:
    result = generate_team_recipe(
        health_condition=None,
        health_goal=None,
        active_inventories=["양파", "당근", "달걀"],
        additional_ingredients=[],
        exclude_ingredients=[],
    )

    assert result["title"] != "주꾸미머리순대"
