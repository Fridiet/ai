import json
import logging
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from .ingredient_matcher import get_ingredient_catalog, lookup_key
from .ingredient_taxonomy import parents
from .ml.inference import RecipeModelScore, get_two_tower_recommender
from .ml.preferences import ingredient_taste_scores
from .quantity_balance import ingredient_balances
from .inventory_state import usable_inventory
from .recommendation import (
    MISSING_REQUIRED_PENALTY,
    PERSONALIZED_INVENTORY_WEIGHT,
    PERSONALIZED_NUTRITION_WEIGHT,
    SCORE_WEIGHTS,
    expiry_urgency,
)


RECIPE_DATASET_PATH = Path(__file__).with_name("data") / "recipes_ko.json"
logger = logging.getLogger(__name__)


class TeamRecipeNotFoundError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _load_recipes() -> list[dict[str, Any]]:
    with RECIPE_DATASET_PATH.open(encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, list) or not payload:
        raise TeamRecipeNotFoundError("레시피 데이터셋이 비어 있습니다.")
    return payload


def _normalize_ingredient_name(value: str) -> str:
    name = value.strip()
    if not name:
        return ""
    match = get_ingredient_catalog().match(name)
    return match["standard_name"] or name


def _normalized_name_set(values: list[str] | None) -> set[str]:
    return {
        normalized
        for value in values or []
        if (normalized := _normalize_ingredient_name(value))
    }


def _health_flags(health_condition: str | None, health_goal: str | None) -> set[str]:
    value = lookup_key(f"{health_condition or ''} {health_goal or ''}")
    flags: set[str] = set()
    if any(keyword in value for keyword in ("당뇨", "혈당", "diabetes")):
        flags.add("diabetes")
    if any(keyword in value for keyword in ("다이어트", "감량", "체중", "저열량", "weightloss")):
        flags.add("weight_loss")
    if any(keyword in value for keyword in ("고혈압", "저염", "나트륨", "lowsodium")):
        flags.add("low_sodium")
    return flags


def _range_score(value: float | int | None, preferred_maximum: float) -> float | None:
    if value is None:
        return None
    numeric_value = float(value)
    if numeric_value <= preferred_maximum:
        return 1.0
    return max(0.0, 1.0 - ((numeric_value - preferred_maximum) / preferred_maximum))


def _health_score(recipe: dict[str, Any], flags: set[str]) -> float:
    if not flags:
        return 0.5

    nutrition = recipe.get("nutrition") or {}
    ingredient_names = {
        ingredient["canonical_name"] for ingredient in recipe.get("ingredients", [])
    }
    scores: list[float] = []
    if "diabetes" in flags:
        carbohydrate_score = _range_score(nutrition.get("carbohydrate_g"), 45.0)
        scores.append(carbohydrate_score if carbohydrate_score is not None else 0.5)
        if "설탕" in ingredient_names:
            scores.append(0.0)
    if "weight_loss" in flags:
        calorie_score = _range_score(nutrition.get("calories_kcal"), 500.0)
        scores.append(calorie_score if calorie_score is not None else 0.5)
    if "low_sodium" in flags:
        sodium_score = _range_score(nutrition.get("sodium_mg"), 700.0)
        scores.append(sodium_score if sodium_score is not None else 0.5)
    return sum(scores) / len(scores) if scores else 0.5


def _as_date(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _as_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed


def _matching_feedback(
    recipe_name: str,
    recent_feedback: list[dict[str, Any]],
    recipe_key: str | None = None,
) -> list[dict[str, Any]]:
    return [
        feedback
        for feedback in recent_feedback
        if ((feedback.get("recipeKey") == recipe_key) if feedback.get("recipeKey") else
            str(feedback.get("recipeName") or "").strip() == recipe_name)
        and feedback.get("occurredAt") is not None
    ]


def _is_recently_dismissed(
    recipe_name: str,
    recent_feedback: list[dict[str, Any]],
    now: datetime,
    recipe_key: str | None = None,
) -> bool:
    # "다른 추천" is a short skip, not an explicit dislike.
    cutoff = now - timedelta(minutes=30)
    recent_skips = sorted([
        event for event in recent_feedback
        if str(event.get('eventType') or '').upper() == 'DISMISSED'
        and event.get('occurredAt') is not None
        and cutoff <= _as_datetime(event['occurredAt']) <= now
    ], key=lambda event: _as_datetime(event['occurredAt']), reverse=True)[:3]
    return any(
        str(feedback.get("eventType") or "").upper() == "DISMISSED"
        and cutoff <= _as_datetime(feedback["occurredAt"]) <= now
        for feedback in _matching_feedback(recipe_name, recent_skips, recipe_key)
    )


def _feedback_diversity_score(
    recipe_name: str,
    recent_feedback: list[dict[str, Any]],
    now: datetime,
    recipe_key: str | None = None,
) -> float:
    feedback = _matching_feedback(recipe_name, recent_feedback, recipe_key)
    cooked_at = [
        _as_datetime(item["occurredAt"])
        for item in feedback
        if str(item.get("eventType") or "").upper() == "COOKED"
    ]
    if cooked_at:
        days_since = (now - max(cooked_at)).days
        if days_since <= 7:
            return 0.0
        if days_since <= 30:
            return SCORE_WEIGHTS["menu_diversity"] / 2

    recently_seen = any(
        str(item.get("eventType") or "").upper() in {"SHOWN", "SAVED"}
        and _as_datetime(item["occurredAt"]) >= now - timedelta(days=1)
        for item in feedback
    )
    if recently_seen:
        return 3.0
    return SCORE_WEIGHTS["menu_diversity"]


def _inventory_by_name(
    inventories: list[dict[str, Any]] | None,
    active_inventories: list[str] | None,
    additional_ingredients: list[str] | None,
    today: date,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    snapshot_names = {_normalize_ingredient_name(str(i.get("ingredientName") or "")) for i in inventories or []}
    for inventory in inventories or []:
        normalized = _normalize_ingredient_name(str(inventory.get("ingredientName") or ""))
        if not normalized:
            continue
        consume_by_date = _as_date(inventory.get("consumeByDate"))
        if consume_by_date is not None and consume_by_date < today:
            continue
        unit_type = inventory.get("unitType", "QUANTITY")
        if not usable_inventory(unit_type, inventory.get("remainingQuantity"), inventory.get("status", "ACTIVE")):
            continue
        grouped.setdefault(normalized, []).append(
            {
                "consume_by_date": consume_by_date,
                "remaining_quantity": inventory.get("remainingQuantity"),
                "unit": inventory.get("unit"),
                "countBasis": inventory.get("countBasis"),
                "perCountQuantity": inventory.get("perCountQuantity"),
                "perCountUnit": inventory.get("perCountUnit"),
                "quantityNeedsReview": inventory.get("quantityNeedsReview", False),
                "unitType": unit_type,
                "customQuantityText": inventory.get("customQuantityText"),
                "status": inventory.get("status", "ACTIVE"),
            }
        )

    # 구버전 클라이언트도 같은 추천 정책을 사용할 수 있도록 이름 목록을 보완한다.
    for name in [*(active_inventories or []), *(additional_ingredients or [])]:
        normalized = _normalize_ingredient_name(name)
        if normalized and normalized not in grouped and normalized not in snapshot_names:
            grouped[normalized] = [
                {"consume_by_date": None, "remaining_quantity": None, "unit": None}
            ]
    return grouped


def _recipe_score(
    recipe: dict[str, Any],
    *,
    inventory_by_name: dict[str, list[dict[str, Any]]],
    health_flags: set[str],
    today: date,
    diversity_score: float,
) -> tuple[
    float,
    dict[str, float | None],
    list[str],
    list[str],
    list[dict[str, Any]],
]:
    ingredients = recipe.get("ingredients", [])
    required = [ingredient for ingredient in ingredients if not ingredient.get("optional", False)]
    if not required:
        return 0.0, {}, [], [], []

    matched_required = [
        ingredient
        for ingredient in required
        if ingredient["canonical_name"] in inventory_by_name
    ]
    missing_required = [
        ingredient
        for ingredient in required
        if ingredient["canonical_name"] not in inventory_by_name
    ]
    matched_all = [
        ingredient
        for ingredient in ingredients
        if ingredient["canonical_name"] in inventory_by_name
    ]

    urgency_values: list[float] = []
    expiring: list[dict[str, Any]] = []
    for ingredient in matched_all:
        snapshots = inventory_by_name[ingredient["canonical_name"]]
        dated = [item for item in snapshots if item["consume_by_date"] is not None]
        if not dated:
            continue
        selected = min(dated, key=lambda item: item["consume_by_date"])
        days_remaining = (selected["consume_by_date"] - today).days
        urgency_values.append(expiry_urgency(days_remaining))
        if days_remaining <= 7:
            expiring.append(
                {
                    "canonical_name": ingredient["canonical_name"],
                    "consume_by_date": selected["consume_by_date"],
                    "days_remaining": days_remaining,
                }
            )

    expiry_score = 0.0
    if urgency_values:
        urgency = 0.7 * max(urgency_values) + 0.3 * (
            sum(urgency_values) / len(urgency_values)
        )
        expiry_score = SCORE_WEIGHTS["expiry_urgency"] * urgency

    coverage = len(matched_required) / len(required)
    coverage_score = SCORE_WEIGHTS["inventory_coverage"] * coverage
    stock_score = SCORE_WEIGHTS["stock_utilization"] * min(1.0, len(matched_all) / 3.0)
    missing_penalty = MISSING_REQUIRED_PENALTY * len(missing_required)
    base_score = max(
        0.0,
        min(
            100.0,
            expiry_score
            + coverage_score
            + stock_score
            + diversity_score
            - missing_penalty,
        ),
    )

    nutrition_fit_score: float | None = None
    total_score = base_score
    if health_flags:
        nutrition_fit_score = 100.0 * _health_score(recipe, health_flags)
        total_score = (
            base_score * PERSONALIZED_INVENTORY_WEIGHT
            + nutrition_fit_score * PERSONALIZED_NUTRITION_WEIGHT
        )

    expiring.sort(key=lambda item: item["days_remaining"])
    breakdown: dict[str, float | None] = {
        "expiry_urgency": round(expiry_score, 2),
        "inventory_coverage": round(coverage_score, 2),
        "stock_utilization": round(stock_score, 2),
        "menu_diversity": round(diversity_score, 2),
        "missing_required_penalty": round(missing_penalty, 2),
        "base_inventory_score": round(base_score, 2),
        "nutrition_fit_score": (
            round(nutrition_fit_score, 2) if nutrition_fit_score is not None else None
        ),
    }
    return (
        round(total_score, 2),
        breakdown,
        [item["canonical_name"] for item in matched_all],
        [item["canonical_name"] for item in missing_required],
        expiring,
    )


def _recommendation_reasons(*, available, missing, expiring, breakdown,
                            personalization_weight=0, target_fallback=False,
                            target_names=(), inventory_names=(), quantity_unknown=()):
    """Display-only summary; detailed calculation remains in scoreBreakdown."""
    def names(values):
        values = list(values)
        return ', '.join(values[:3]) + (f' 외 {len(values)-3}개' if len(values) > 3 else '')

    reasons = []
    if target_fallback:
        reasons.append(f'요청 재료({names(sorted(target_names))}) 조건에 맞는 메뉴가 없어 대체 추천했어요.')
        unavailable = sorted(set(target_names) - set(inventory_names))
        if unavailable:
            reasons.append(f'재고가 없거나 소비기한이 지난 재료: {names(unavailable)}.')
    if expiring:
        first = expiring[0]
        reasons.append(f"{first['canonical_name']}의 소비기한이 오늘까지입니다." if first['days_remaining'] == 0
                       else f"{first['canonical_name']} 소비기한: {first['days_remaining']}일 남음.")
    if available:
        reasons.append(f'보유 재료: {names(available)}.')
    if missing:
        reasons.append(f'추가 재료: {names(missing)}.')
    if quantity_unknown:
        reasons.append(f'잔량 확인 필요: {names(quantity_unknown)}.')
    health = breakdown.get('nutrition_fit_score') is not None
    if personalization_weight > 0:
        reasons.append('건강 목표와 저장·조리 이력을 반영했어요.' if health
                       else '저장·조리 이력을 반영했어요.')
    elif health:
        reasons.append('건강 목표를 반영했어요.')
    elif (breakdown.get('menu_diversity') or 0.0) < SCORE_WEIGHTS['menu_diversity']:
        reasons.append('최근 메뉴 반복을 고려했어요.')
    return reasons


def generate_team_recipe(
    *,
    health_condition: str | None,
    health_goal: str | None,
    active_inventories: list[str] | None,
    additional_ingredients: list[str] | None,
    exclude_ingredients: list[str] | None,
    target_ingredients: list[str] | None = None,
    inventories: list[dict[str, Any]] | None = None,
    recent_feedback: list[dict[str, Any]] | None = None,
    shopping_mode: bool = False,
    today: date | None = None,
    now: datetime | None = None,
    ranking_results: list[dict[str, Any]] | None = None,
    allow_target_fallback: bool = False,
) -> dict[str, Any]:
    today_value = today or date.today()
    now_value = now or (datetime.combine(today_value, time.max) if today else datetime.now())
    feedback_items = recent_feedback or []
    inventory_by_name = _inventory_by_name(
        inventories,
        active_inventories,
        additional_ingredients,
        today_value,
    )
    target_names = _normalized_name_set(target_ingredients)
    excluded_names = _normalized_name_set(exclude_ingredients)
    excluded_names |= {child for child, parent in parents().items() if parent in excluded_names}
    flags = _health_flags(health_condition, health_goal)
    two_tower = get_two_tower_recommender()
    model_scores: dict[str, RecipeModelScore] = {}
    personalization_weight = 0.0
    positive_event_count = 0
    content_scores, content_confidence = ingredient_taste_scores(_load_recipes(), feedback_items, now_value)
    if two_tower is not None and inventory_by_name:
        try:
            prediction = two_tower.predict(
                inventory_by_name=inventory_by_name,
                health_condition=health_condition,
                health_goal=health_goal,
                today=today_value,
                recent_feedback=feedback_items,
                now=now_value,
            )
            model_scores = prediction.scores_by_recipe_id
            personalization_weight = prediction.personalization_weight
            positive_event_count = prediction.positive_event_count
        except (TypeError, ValueError, RuntimeError) as exc:
            logger.warning(
                "Two-Tower 추론에 실패해 규칙 기반 추천으로 전환합니다: %s",
                exc,
            )

    ranked: list[
        tuple[
            float,
            dict[str, Any],
            dict[str, float | None],
            list[str],
            list[str],
            list[dict[str, Any]],
        ]
    ] = []
    for recipe in _load_recipes():
        recipe_key = f"{recipe.get('source') or 'LOCAL'}:{recipe['source_recipe_id']}"
        ingredients = recipe.get("ingredients", [])
        ingredient_names = {
            ingredient["canonical_name"] for ingredient in ingredients
        }
        primary_names = {
            ingredient["canonical_name"]
            for ingredient in ingredients
            if ingredient.get("primary", False)
        }
        if not ingredients or any(
            ingredient["canonical_name"] in excluded_names for ingredient in ingredients
        ):
            continue
        if primary_names and not primary_names.issubset(inventory_by_name):
            continue
        if target_names and not allow_target_fallback and not target_names.issubset(ingredient_names):
            continue
        if _is_recently_dismissed(
            recipe["recipe_name"],
            feedback_items,
            now_value,
            recipe_key,
        ):
            continue

        diversity_score = _feedback_diversity_score(
            recipe["recipe_name"],
            feedback_items,
            now_value,
            recipe_key,
        )

        score, breakdown, available, missing, expiring = _recipe_score(
            recipe,
            inventory_by_name=inventory_by_name,
            health_flags=flags,
            today=today_value,
            diversity_score=diversity_score,
        )
        if not available:
            continue
        model_score = model_scores.get(str(recipe.get("source_recipe_id") or ""))
        if model_score is not None:
            breakdown["two_tower_score"] = round(model_score.percentile_score, 2)
            breakdown["two_tower_similarity"] = round(model_score.similarity, 4)
            breakdown["context_similarity"] = model_score.context_similarity
            breakdown["taste_similarity"] = model_score.taste_similarity
            breakdown["personalization_weight"] = round(personalization_weight, 4)
            breakdown["positive_event_count"] = float(positive_event_count)
            feedback_penalty = max(
                0.0,
                SCORE_WEIGHTS["menu_diversity"] - diversity_score,
            )
            score = round(
                max(0.0, min(100.0, model_score.percentile_score - feedback_penalty)),
                2,
            )
            if personalization_weight > 0:
                # Percentile gaps between similar top recipes are tiny. Applying the
                # old 10-point diversity penalty there overwhelmed positive history.
                # Rank personalized candidates by their actual vector similarity;
                # cap the recent-repeat deduction at 2.5 display points.
                similarity_score = 50.0 * (1.0 + model_score.similarity)
                repeat_penalty = feedback_penalty * 0.25
                breakdown["personalized_similarity_score"] = round(similarity_score, 2)
                breakdown["personalized_repeat_penalty"] = round(repeat_penalty, 2)
                score = round(max(0.0, min(100.0, similarity_score - repeat_penalty)), 2)
        if personalization_weight > 0 and recipe_key in content_scores:
            content_weight = 0.30 * (content_confidence / 0.65)
            content_score = 100.0 * content_scores[recipe_key]
            breakdown['ingredient_taste_score'] = round(content_score, 2)
            breakdown['ingredient_taste_weight'] = round(content_weight, 4)
            score = round((1-content_weight)*score + content_weight*content_score, 2)
        ranked.append((score, recipe, breakdown, available, missing, expiring))

    shopping_counts = {}
    if shopping_mode:
        # Recommend modest additions that use existing stock, never a whole unsolicited shop.
        for item in ranked:
            balances = ingredient_balances(item[1]["ingredients"], inventory_by_name)
            shopping_counts[id(item)] = sum(b["status"] == "SHORTAGE" or
                (b["status"] == "UNKNOWN" and b["name"] in item[4]) for b in balances)
        ranked = [item for item in ranked if 1 <= shopping_counts[id(item)] <= 3 and item[3]]
    target_fallback = False
    if target_names and allow_target_fallback:
        targeted = [item for item in ranked if target_names.issubset({
            ingredient['canonical_name'] for ingredient in item[1]['ingredients']
        })]
        if targeted:
            ranked = targeted
        elif ranked:
            # Only the preferred target is relaxed. Expiry, exclusions, primary
            # ingredient availability and shopping constraints remain unchanged.
            target_fallback = True
    if not ranked:
        if target_names:
            targets = ", ".join(sorted(target_names))
            raise TeamRecipeNotFoundError(
                f"{targets}을(를) 포함한 추천 레시피가 없습니다."
            )
        raise TeamRecipeNotFoundError("현재 재고와 연결되는 추가 구매 추천이 없습니다." if shopping_mode else "현재 재고 조건을 만족하는 레시피가 없습니다.")

    missing_count = lambda item: shopping_counts[id(item)] if shopping_mode else len(item[4])
    minimum_missing_count = min(missing_count(item) for item in ranked)
    feasible_ranked = [
        item for item in ranked if missing_count(item) == minimum_missing_count
    ]
    if ranking_results is not None:
        ranking_results.extend([
            {"title": item[1]["recipe_name"], "score": item[0],
             "ingredients": [i['canonical_name'] for i in item[1]['ingredients']],
             "scoreBreakdown": item[2]}
            for item in sorted(feasible_ranked, key=lambda item: (
                item[0], item[2]['expiry_urgency'] or 0.0,
                -(item[1].get('cook_time') or 999), item[1].get('source_recipe_id', '')),
                reverse=True)
        ])
    score, selected, breakdown, available, missing, expiring = max(
        feasible_ranked,
        key=lambda item: (
            item[0],
            item[2]["expiry_urgency"] or 0.0,
            -(item[1].get("cook_time") or 999),
            item[1].get("source_recipe_id", ""),
        ),
    )
    steps = selected.get("steps") or []
    if not steps:
        raise TeamRecipeNotFoundError("선택된 레시피의 조리 순서가 올바르지 않습니다.")
    cooking_time = selected.get("cook_time") or max(5, len(steps) * 5)

    balances = ingredient_balances(selected["ingredients"], inventory_by_name)
    reasons = _recommendation_reasons(
        available=available, missing=missing, expiring=expiring, breakdown=breakdown,
        personalization_weight=personalization_weight, target_fallback=target_fallback,
        target_names=target_names, inventory_names=inventory_by_name.keys(),
        quantity_unknown=list(dict.fromkeys(b['name'] for b in balances
                                          if b['status'] == 'UNKNOWN' and b['name'] in available)))

    return {
        "recipeKey": f"{selected.get('source') or 'LOCAL'}:{selected['source_recipe_id']}",
        "title": selected["recipe_name"],
        # Source metadata stays in the dataset; do not use attribution as a UI subtitle.
        "description": "" if (selected.get('source') == 'MFDS_COOKRCP01'
                              or '식품의약품안전처 조리식품' in (selected.get('description') or ''))
                       else selected.get("description") or f"{selected['recipe_name']} 레시피입니다.",
        "cookingTime": int(cooking_time),
        "category": selected.get("category") or "기타",
        "score": score,
        "scoreBreakdown": breakdown,
        "reasons": reasons,
        "availableIngredients": available,
        "missingIngredients": missing,
        "ingredientBalances": balances,
        "expiringIngredients": expiring,
        "ingredients": [
            {
                "nameText": ingredient["canonical_name"],
                "quantity": float(ingredient["quantity"]),
                "unit": ingredient["unit"],
                "optional": bool(ingredient.get("optional", False)),
            }
            for ingredient in selected["ingredients"]
        ],
        "steps": [
            {"stepNumber": index, "description": str(description)}
            for index, description in enumerate(steps, start=1)
        ],
    }
