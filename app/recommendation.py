import json
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .database import (
    DatabaseSchemaMissingError,
    OcrContextValidationError,
    _validate_ocr_context,
    get_engine,
)


SCORE_WEIGHTS = {
    "expiry_urgency": 40.0,
    "inventory_coverage": 35.0,
    "stock_utilization": 15.0,
    "menu_diversity": 10.0,
}
MISSING_REQUIRED_PENALTY = 10.0
PERSONALIZED_INVENTORY_WEIGHT = 0.70
PERSONALIZED_NUTRITION_WEIGHT = 0.30
SUPPORTED_PROFILE_GOALS = {
    "GENERAL",
    "WEIGHT_LOSS",
    "DIABETES_MANAGEMENT",
    "LOW_SODIUM",
}


def expiry_urgency(days_remaining: int | None) -> float:
    if days_remaining is None:
        return 0.0
    if days_remaining < 0:
        return 0.0
    if days_remaining == 0:
        return 1.0
    if days_remaining == 1:
        return 0.95
    if days_remaining == 2:
        return 0.90
    if days_remaining == 3:
        return 0.80
    if days_remaining == 4:
        return 0.65
    if days_remaining == 5:
        return 0.55
    if days_remaining == 6:
        return 0.45
    if days_remaining == 7:
        return 0.35
    if days_remaining <= 14:
        return 0.15
    return 0.0


def _diversity_score(recipe_name: str, recent_meals: list[dict], now: datetime) -> float:
    matched = [meal for meal in recent_meals if meal["title"].strip() == recipe_name]
    if not matched:
        return SCORE_WEIGHTS["menu_diversity"]
    last_eaten = max(meal["eaten_at"] for meal in matched)
    days_since = (now - last_eaten).days
    if days_since <= 7:
        return 0.0
    if days_since <= 30:
        return 5.0
    return SCORE_WEIGHTS["menu_diversity"]


def _decode_steps(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return [str(step) for step in value]
    decoded = json.loads(value)
    if not isinstance(decoded, list):
        raise ValueError("steps_json은 JSON 배열이어야 합니다.")
    return [str(step) for step in decoded]


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _optional_float(value) -> float | None:
    return None if value is None else float(value)


def _parse_json_list(value: str | list | None) -> list[str]:
    if value is None:
        return []
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, list):
        return []
    return [str(item) for item in decoded]


def _build_profile(row: dict | None) -> dict | None:
    if row is None:
        return None
    goal = str(row["goal"])
    if goal not in SUPPORTED_PROFILE_GOALS:
        raise ValueError(f"지원하지 않는 식단 목표입니다: {goal}")
    return {
        "goal": goal,
        "conditions": _parse_json_list(row["conditions_json"]),
        "calorie_min": _optional_float(row["calorie_min"]),
        "calorie_max": _optional_float(row["calorie_max"]),
        "carbohydrate_min_g": _optional_float(row["carbohydrate_min_g"]),
        "carbohydrate_max_g": _optional_float(row["carbohydrate_max_g"]),
        "protein_min_g": _optional_float(row["protein_min_g"]),
        "sodium_max_mg": _optional_float(row["sodium_max_mg"]),
    }


def _range_fit(value: float, minimum: float | None, maximum: float | None) -> float:
    if minimum is not None and value < minimum:
        return max(0.0, value / minimum) if minimum > 0 else 0.0
    if maximum is not None and value > maximum:
        return max(0.0, 1.0 - ((value - maximum) / maximum)) if maximum > 0 else 0.0
    return 1.0


def _nutrition_facts(recipe: dict) -> dict | None:
    if recipe["calories_kcal"] is None or not recipe["nutrition_source"]:
        return None
    return {
        "serving_size_g": _optional_float(recipe["serving_size_g"]),
        "calories_kcal": float(recipe["calories_kcal"]),
        "carbohydrate_g": _optional_float(recipe["carbohydrate_g"]),
        "protein_g": _optional_float(recipe["protein_g"]),
        "fat_g": _optional_float(recipe["fat_g"]),
        "sodium_mg": _optional_float(recipe["sodium_mg"]),
        "source": str(recipe["nutrition_source"]),
    }


def _nutrition_fit(nutrition: dict, profile: dict) -> tuple[float | None, list[str]]:
    scores: list[float] = []
    reasons: list[str] = []

    calorie_min = profile["calorie_min"]
    calorie_max = profile["calorie_max"]
    if calorie_min is not None or calorie_max is not None:
        score = _range_fit(nutrition["calories_kcal"], calorie_min, calorie_max)
        scores.append(score)
        if score == 1.0:
            reasons.append(f"열량 {nutrition['calories_kcal']:g}kcal가 설정 범위에 포함됩니다")
        else:
            reasons.append(f"열량 {nutrition['calories_kcal']:g}kcal가 설정 범위를 벗어납니다")

    carbohydrate_min = profile["carbohydrate_min_g"]
    carbohydrate_max = profile["carbohydrate_max_g"]
    if carbohydrate_min is not None or carbohydrate_max is not None:
        value = nutrition["carbohydrate_g"]
        if value is not None:
            score = _range_fit(value, carbohydrate_min, carbohydrate_max)
            scores.append(score)
            if score == 1.0:
                reasons.append(f"탄수화물 {value:g}g이 설정 범위에 포함됩니다")
            else:
                reasons.append(f"탄수화물 {value:g}g이 설정 범위를 벗어납니다")

    protein_min = profile["protein_min_g"]
    if protein_min is not None and nutrition["protein_g"] is not None:
        value = nutrition["protein_g"]
        score = _range_fit(value, protein_min, None)
        scores.append(score)
        if score == 1.0:
            reasons.append(f"단백질 {value:g}g이 설정 최솟값을 충족합니다")
        else:
            reasons.append(f"단백질 {value:g}g이 설정 최솟값보다 적습니다")

    sodium_max = profile["sodium_max_mg"]
    if sodium_max is not None and nutrition["sodium_mg"] is not None:
        value = nutrition["sodium_mg"]
        score = _range_fit(value, None, sodium_max)
        scores.append(score)
        if score == 1.0:
            reasons.append(f"나트륨 {value:g}mg이 설정 상한 이내입니다")
        else:
            reasons.append(f"나트륨 {value:g}mg이 설정 상한을 초과합니다")

    if not scores:
        return None, ["비교할 영양 목표가 설정되지 않았습니다"]
    return round(100.0 * sum(scores) / len(scores), 2), reasons


def _reason(
    matched_count: int,
    required_count: int,
    expiring: list[dict],
    missing: list[str],
) -> str:
    parts: list[str] = []
    if expiring:
        first = expiring[0]
        if first["days_remaining"] == 0:
            parts.append(f"{first['canonical_name']}의 소비기한이 오늘까지입니다")
        else:
            parts.append(
                f"{first['canonical_name']}의 소비기한이 {first['days_remaining']}일 남았습니다"
            )
    parts.append(f"필수 재료 {required_count}개 중 {matched_count}개를 보유하고 있습니다")
    if missing:
        parts.append(f"추가로 필요한 재료는 {', '.join(missing)}입니다")
    return ". ".join(parts) + "."


def recommend_recipes(
    *,
    fridge_id: int,
    user_id: int,
    limit: int = 5,
    max_missing_required: int = 1,
    engine: Engine | None = None,
    today: date | None = None,
) -> dict:
    target_engine = engine or get_engine()
    table_names = set(inspect(target_engine).get_table_names())
    required_tables = {
        "inventory_batches",
        "ingredient_master",
        "recipe_catalog",
        "recipe_catalog_ingredients",
        "user_diet_profiles",
        "user_excluded_ingredients",
    }
    missing_tables = required_tables - table_names
    if missing_tables:
        raise DatabaseSchemaMissingError(
            f"추천에 필요한 테이블이 없습니다: {', '.join(sorted(missing_tables))}"
        )
    recipe_columns = {column["name"] for column in inspect(target_engine).get_columns("recipe_catalog")}
    required_nutrition_columns = {
        "serving_size_g",
        "calories_kcal",
        "carbohydrate_g",
        "protein_g",
        "fat_g",
        "sodium_mg",
        "nutrition_source",
    }
    missing_columns = required_nutrition_columns - recipe_columns
    if missing_columns:
        raise DatabaseSchemaMissingError(
            "레시피 영양 컬럼이 없습니다. migrations/004_add_personalized_nutrition.sql을 적용하세요."
        )

    today_value = today or date.today()
    now = datetime.combine(today_value, datetime.min.time())

    with Session(target_engine) as session:
        _validate_ocr_context(session, table_names, fridge_id, user_id)
        inventory_rows = session.execute(
            text(
                "SELECT b.batch_id, b.ingredient_id, m.canonical_name, b.consume_by_date, "
                "b.remaining_quantity, b.unit, b.storage_type "
                "FROM inventory_batches b "
                "JOIN ingredient_master m ON m.ingredient_id=b.ingredient_id "
                "WHERE b.fridge_id=:fridge_id AND b.status='ACTIVE' "
                "AND b.remaining_quantity > 0"
            ),
            {"fridge_id": fridge_id},
        ).mappings().all()

        recipe_rows = session.execute(
            text(
                "SELECT recipe_catalog_id, recipe_name, cook_time, category, description, "
                "steps_json, source, serving_size_g, calories_kcal, carbohydrate_g, "
                "protein_g, fat_g, sodium_mg, nutrition_source FROM recipe_catalog"
            )
        ).mappings().all()
        ingredient_rows = session.execute(
            text(
                "SELECT recipe_catalog_id, ingredient_id, canonical_name, quantity, unit, optional "
                "FROM recipe_catalog_ingredients ORDER BY recipe_catalog_ingredient_id"
            )
        ).mappings().all()

        profile_row = session.execute(
            text(
                "SELECT goal, conditions_json, calorie_min, calorie_max, carbohydrate_min_g, "
                "carbohydrate_max_g, protein_min_g, sodium_max_mg "
                "FROM user_diet_profiles WHERE user_id=:user_id AND enabled=1 LIMIT 1"
            ),
            {"user_id": user_id},
        ).mappings().one_or_none()
        excluded_ingredient_ids = set(
            session.execute(
                text(
                    "SELECT ingredient_id FROM user_excluded_ingredients "
                    "WHERE user_id=:user_id"
                ),
                {"user_id": user_id},
            ).scalars()
        )

        recent_meals: list[dict] = []
        if "meal_events" in table_names:
            recent_meals = [
                dict(row)
                for row in session.execute(
                    text(
                        "SELECT title, eaten_at FROM meal_events "
                        "WHERE fridge_id=:fridge_id AND eaten_at>=:since"
                    ),
                    {"fridge_id": fridge_id, "since": now - timedelta(days=30)},
                ).mappings()
            ]

    profile = _build_profile(dict(profile_row) if profile_row is not None else None)
    personalized = profile is not None and profile["goal"] != "GENERAL"
    response_warnings = []
    if personalized:
        response_warnings.append(
            "영양 적합도는 설정값과 데이터셋 영양정보를 비교한 참고 정보이며 의료 진단이나 치료를 대체하지 않습니다."
        )

    active_inventory_count = len(inventory_rows)
    expired_inventory_rows = [
        row
        for row in inventory_rows
        if row["consume_by_date"] is not None
        and _as_date(row["consume_by_date"]) < today_value
    ]
    usable_inventory_rows = [
        row
        for row in inventory_rows
        if row["consume_by_date"] is None
        or _as_date(row["consume_by_date"]) >= today_value
    ]

    inventory_by_ingredient: dict[int, list[dict]] = defaultdict(list)
    for row in usable_inventory_rows:
        inventory_by_ingredient[row["ingredient_id"]].append(dict(row))

    ingredients_by_recipe: dict[int, list[dict]] = defaultdict(list)
    for row in ingredient_rows:
        ingredients_by_recipe[row["recipe_catalog_id"]].append(dict(row))

    recommendations: list[dict] = []
    for recipe in recipe_rows:
        recipe_ingredients = ingredients_by_recipe[recipe["recipe_catalog_id"]]
        if any(
            item["ingredient_id"] in excluded_ingredient_ids for item in recipe_ingredients
        ):
            continue
        nutrition = _nutrition_facts(dict(recipe))
        if personalized and nutrition is None:
            continue
        required_ingredients = [item for item in recipe_ingredients if not item["optional"]]
        if not required_ingredients:
            continue

        available_required = [
            item for item in required_ingredients if item["ingredient_id"] in inventory_by_ingredient
        ]
        missing_required = [
            item for item in required_ingredients if item["ingredient_id"] not in inventory_by_ingredient
        ]
        if not available_required or len(missing_required) > max_missing_required:
            continue

        matched_all = [
            item for item in recipe_ingredients if item["ingredient_id"] in inventory_by_ingredient
        ]
        urgency_values: list[float] = []
        expiring: list[dict] = []
        used_batch_ids: list[int] = []
        for ingredient in matched_all:
            batches = inventory_by_ingredient[ingredient["ingredient_id"]]
            dated_batches = [batch for batch in batches if batch["consume_by_date"] is not None]
            selected_batch = (
                min(dated_batches, key=lambda batch: _as_date(batch["consume_by_date"]))
                if dated_batches
                else batches[0]
            )
            used_batch_ids.append(selected_batch["batch_id"])
            consume_by_date = (
                _as_date(selected_batch["consume_by_date"])
                if selected_batch["consume_by_date"] is not None
                else None
            )
            days_remaining = (
                (consume_by_date - today_value).days if consume_by_date is not None else None
            )
            urgency_values.append(expiry_urgency(days_remaining))
            if days_remaining is not None and days_remaining <= 7:
                expiring.append(
                    {
                        "batch_id": selected_batch["batch_id"],
                        "ingredient_id": ingredient["ingredient_id"],
                        "canonical_name": ingredient["canonical_name"],
                        "consume_by_date": consume_by_date,
                        "days_remaining": days_remaining,
                    }
                )

        expiring.sort(key=lambda item: item["days_remaining"])
        expiry_score = 0.0
        if urgency_values:
            urgency = 0.7 * max(urgency_values) + 0.3 * (
                sum(urgency_values) / len(urgency_values)
            )
            expiry_score = SCORE_WEIGHTS["expiry_urgency"] * urgency

        coverage_ratio = len(available_required) / len(required_ingredients)
        coverage_score = SCORE_WEIGHTS["inventory_coverage"] * coverage_ratio
        stock_score = SCORE_WEIGHTS["stock_utilization"] * min(1.0, len(matched_all) / 3.0)
        diversity_score = _diversity_score(recipe["recipe_name"], recent_meals, now)
        missing_penalty = MISSING_REQUIRED_PENALTY * len(missing_required)
        base_score = max(
            0.0,
            min(100.0, expiry_score + coverage_score + stock_score + diversity_score - missing_penalty),
        )
        nutrition_fit_score = None
        health_reasons: list[str] = []
        if personalized and nutrition is not None and profile is not None:
            nutrition_fit_score, health_reasons = _nutrition_fit(nutrition, profile)
            if nutrition_fit_score is None:
                continue
            total_score = (
                base_score * PERSONALIZED_INVENTORY_WEIGHT
                + nutrition_fit_score * PERSONALIZED_NUTRITION_WEIGHT
            )
        else:
            total_score = base_score

        recommendations.append(
            {
                "recipe_id": recipe["recipe_catalog_id"],
                "recipe_name": recipe["recipe_name"],
                "cook_time": recipe["cook_time"],
                "category": recipe["category"],
                "description": recipe["description"],
                "steps": _decode_steps(recipe["steps_json"]),
                "source": recipe["source"],
                "nutrition": nutrition,
                "health_reasons": health_reasons,
                "total_score": round(total_score, 2),
                "score_breakdown": {
                    "expiry_urgency": round(expiry_score, 2),
                    "inventory_coverage": round(coverage_score, 2),
                    "stock_utilization": round(stock_score, 2),
                    "menu_diversity": round(diversity_score, 2),
                    "missing_required_penalty": round(missing_penalty, 2),
                    "base_inventory_score": round(base_score, 2),
                    "nutrition_fit_score": nutrition_fit_score,
                },
                "coverage_ratio": round(coverage_ratio, 4),
                "available_ingredients": [item["canonical_name"] for item in matched_all],
                "missing_ingredients": [item["canonical_name"] for item in missing_required],
                "expiring_ingredients": expiring,
                "used_batch_ids": sorted(set(used_batch_ids)),
                "reason": _reason(
                    len(available_required),
                    len(required_ingredients),
                    expiring,
                    [item["canonical_name"] for item in missing_required],
                ),
            }
        )

    recommendations.sort(
        key=lambda item: (
            item["total_score"],
            item["score_breakdown"]["expiry_urgency"],
            item["coverage_ratio"],
            -(item["cook_time"] or 999),
        ),
        reverse=True,
    )

    return {
        "fridge_id": fridge_id,
        "user_id": user_id,
        "generated_at": datetime.now(),
        "active_inventory_count": active_inventory_count,
        "excluded_expired_inventory_count": len(expired_inventory_rows),
        "applied_profile": profile,
        "warnings": response_warnings,
        "score_policy": {
            **SCORE_WEIGHTS,
            "missing_required_penalty_per_item": MISSING_REQUIRED_PENALTY,
            "personalized_inventory_weight": PERSONALIZED_INVENTORY_WEIGHT,
            "personalized_nutrition_weight": PERSONALIZED_NUTRITION_WEIGHT,
        },
        "recommendations": recommendations[:limit],
    }
