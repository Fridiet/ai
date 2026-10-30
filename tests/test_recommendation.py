import json
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, text

from app.recommendation import expiry_urgency, recommend_recipes


def _engine_with_recommendation_data():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    statements = [
        "CREATE TABLE users (user_id INTEGER PRIMARY KEY)",
        "CREATE TABLE fridges (fridge_id INTEGER PRIMARY KEY, owner_user_id INTEGER NOT NULL)",
        "CREATE TABLE fridge_members (fridge_id INTEGER, user_id INTEGER, status TEXT)",
        "CREATE TABLE ingredient_master (ingredient_id INTEGER PRIMARY KEY, canonical_name TEXT)",
        """
        CREATE TABLE inventory_batches (
            batch_id INTEGER PRIMARY KEY,
            fridge_id INTEGER,
            ingredient_id INTEGER,
            consume_by_date DATE,
            remaining_quantity REAL,
            unit TEXT,
            storage_type TEXT,
            status TEXT
        )
        """,
        """
        CREATE TABLE recipe_catalog (
            recipe_catalog_id INTEGER PRIMARY KEY,
            recipe_name TEXT,
            cook_time INTEGER,
            category TEXT,
            description TEXT,
            steps_json TEXT,
            source TEXT,
            serving_size_g REAL,
            calories_kcal REAL,
            carbohydrate_g REAL,
            protein_g REAL,
            fat_g REAL,
            sodium_mg REAL,
            nutrition_source TEXT
        )
        """,
        """
        CREATE TABLE user_diet_profiles (
            user_id INTEGER PRIMARY KEY,
            goal TEXT,
            conditions_json TEXT,
            calorie_min REAL,
            calorie_max REAL,
            carbohydrate_min_g REAL,
            carbohydrate_max_g REAL,
            protein_min_g REAL,
            sodium_max_mg REAL,
            enabled INTEGER
        )
        """,
        "CREATE TABLE user_excluded_ingredients (user_id INTEGER, ingredient_id INTEGER, reason TEXT)",
        """
        CREATE TABLE recipe_catalog_ingredients (
            recipe_catalog_ingredient_id INTEGER PRIMARY KEY,
            recipe_catalog_id INTEGER,
            ingredient_id INTEGER,
            canonical_name TEXT,
            quantity REAL,
            unit TEXT,
            optional INTEGER
        )
        """,
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        connection.execute(text("INSERT INTO users VALUES (1)"))
        connection.execute(text("INSERT INTO fridges VALUES (1, 1)"))
        connection.execute(text("INSERT INTO fridge_members VALUES (1, 1, 'ACTIVE')"))
        connection.execute(
            text(
                "INSERT INTO user_diet_profiles VALUES "
                "(1, 'GENERAL', '[]', NULL, NULL, NULL, NULL, NULL, NULL, 1)"
            )
        )
        connection.execute(
            text("INSERT INTO ingredient_master VALUES (1, '두부'), (2, '달걀'), (3, '소금')")
        )
        connection.execute(
            text(
                "INSERT INTO inventory_batches VALUES "
                "(10, 1, 1, '2026-08-18', 300, 'g', 'REFRIGERATED', 'ACTIVE'), "
                "(11, 1, 2, '2026-08-25', 10, '개', 'REFRIGERATED', 'ACTIVE'), "
                "(12, 1, 3, '2026-08-14', 100, 'g', 'ROOM_TEMPERATURE', 'ACTIVE')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO recipe_catalog VALUES "
                "(100, '두부 달걀전', 20, '반찬', '재고 활용 반찬', :steps, 'TEST', "
                "250, 250, 10, 20, 12, 300, 'TEST_NUTRITION'), "
                "(101, '소금 요리', 5, '기타', '만료 재고 제외 검증', :steps, 'TEST', "
                "100, 100, 5, 1, 0, 800, 'TEST_NUTRITION')"
            ),
            {"steps": json.dumps(["두부와 달걀을 섞습니다.", "팬에 굽습니다."], ensure_ascii=False)},
        )
        connection.execute(
            text(
                "INSERT INTO recipe_catalog_ingredients VALUES "
                "(1, 100, 1, '두부', 300, 'g', 0), "
                "(2, 100, 2, '달걀', 2, '개', 0), "
                "(3, 100, 3, '소금', NULL, NULL, 1), "
                "(4, 101, 3, '소금', 10, 'g', 0)"
            )
        )
    return engine


def test_expiry_urgency_policy() -> None:
    assert expiry_urgency(-1) == 0.0
    assert expiry_urgency(3) == 0.8
    assert expiry_urgency(7) == 0.35
    assert expiry_urgency(14) == 0.15
    assert expiry_urgency(15) == 0.0
    assert expiry_urgency(None) == 0.0


def test_recommend_recipes_prioritizes_expiring_inventory() -> None:
    result = recommend_recipes(
        fridge_id=1,
        user_id=1,
        limit=5,
        max_missing_required=0,
        engine=_engine_with_recommendation_data(),
        today=date(2026, 8, 15),
    )

    assert result["active_inventory_count"] == 3
    assert result["excluded_expired_inventory_count"] == 1
    assert len(result["recommendations"]) == 1
    recommendation = result["recommendations"][0]
    assert recommendation["recipe_name"] == "두부 달걀전"
    assert recommendation["coverage_ratio"] == 1.0
    assert recommendation["missing_ingredients"] == []
    assert recommendation["expiring_ingredients"][0]["canonical_name"] == "두부"
    assert recommendation["score_breakdown"]["expiry_urgency"] > 0
    assert recommendation["score_breakdown"]["nutrition_fit_score"] is None


def test_recommend_recipes_applies_user_nutrition_profile() -> None:
    engine = _engine_with_recommendation_data()
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE user_diet_profiles SET goal='DIABETES_MANAGEMENT', "
                "conditions_json='[\"DIABETES\"]', calorie_min=200, calorie_max=400, "
                "carbohydrate_max_g=30, protein_min_g=15, sodium_max_mg=600 WHERE user_id=1"
            )
        )

    result = recommend_recipes(
        fridge_id=1,
        user_id=1,
        max_missing_required=0,
        engine=engine,
        today=date(2026, 8, 15),
    )

    assert result["applied_profile"]["goal"] == "DIABETES_MANAGEMENT"
    assert result["warnings"]
    recommendation = result["recommendations"][0]
    assert recommendation["nutrition"]["calories_kcal"] == 250
    assert recommendation["score_breakdown"]["nutrition_fit_score"] == 100.0
    assert any("탄수화물" in reason for reason in recommendation["health_reasons"])


def test_recommend_recipes_hard_filters_excluded_ingredient() -> None:
    engine = _engine_with_recommendation_data()
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO user_excluded_ingredients VALUES (1, 2, 'ALLERGY')")
        )

    result = recommend_recipes(
        fridge_id=1,
        user_id=1,
        max_missing_required=0,
        engine=engine,
        today=date(2026, 8, 15),
    )

    assert result["recommendations"] == []


def test_recipe_dataset_has_verified_official_nutrition_and_master_mappings() -> None:
    data_dir = Path(__file__).parents[1] / "app" / "data"
    recipes = json.loads((data_dir / "recipes_ko.json").read_text(encoding="utf-8"))
    dictionary = json.loads((data_dir / "ingredients_ko.json").read_text(encoding="utf-8"))
    master_names = {item["name"] for item in dictionary}
    official_recipes = [recipe for recipe in recipes if recipe.get("source") == "MFDS_COOKRCP01"]

    # The repository starts with 20 local recipes and may contain either the
    # five-record MFDS sample or a full import. Dataset refreshes must not make
    # this integrity test depend on one exact download size.
    assert len(recipes) >= 25
    assert len(official_recipes) >= 5
    assert len({recipe["source_recipe_id"] for recipe in recipes}) == len(recipes)
    assert all(recipe["nutrition"]["source"] == "MFDS_COOKRCP01" for recipe in official_recipes)
    assert all(
        ingredient["canonical_name"] in master_names
        for recipe in recipes
        for ingredient in recipe["ingredients"]
    )
