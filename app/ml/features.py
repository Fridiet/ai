from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable

import numpy as np


MODEL_SCHEMA_VERSION = 1
GOAL_NAMES = (
    "GENERAL",
    "WEIGHT_LOSS",
    "DIABETES_MANAGEMENT",
    "LOW_SODIUM",
)
NUMERIC_FIELDS = (
    "calories_kcal",
    "carbohydrate_g",
    "protein_g",
    "fat_g",
    "sodium_mg",
    "cook_time",
)


@dataclass(frozen=True)
class FeatureMetadata:
    schema_version: int
    ingredient_vocab: list[str]
    category_vocab: list[str]
    recipe_ids: list[str]
    numeric_means: list[float]
    numeric_stds: list[float]
    goal_names: list[str]

    @property
    def query_input_size(self) -> int:
        return len(self.ingredient_vocab) * 2 + len(self.goal_names)

    @property
    def recipe_input_size(self) -> int:
        return (
            len(self.ingredient_vocab)
            + len(NUMERIC_FIELDS) * 2
            + len(self.category_vocab)
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["query_input_size"] = self.query_input_size
        payload["recipe_input_size"] = self.recipe_input_size
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FeatureMetadata":
        return cls(
            schema_version=int(payload["schema_version"]),
            ingredient_vocab=[str(value) for value in payload["ingredient_vocab"]],
            category_vocab=[str(value) for value in payload["category_vocab"]],
            recipe_ids=[str(value) for value in payload["recipe_ids"]],
            numeric_means=[float(value) for value in payload["numeric_means"]],
            numeric_stds=[float(value) for value in payload["numeric_stds"]],
            goal_names=[str(value) for value in payload["goal_names"]],
        )


def recipe_id(recipe: dict[str, Any]) -> str:
    value = str(recipe.get("source_recipe_id") or "").strip()
    if not value:
        raise ValueError("source_recipe_id가 없는 레시피는 학습할 수 없습니다.")
    return value


def recipe_ingredient_names(
    recipe: dict[str, Any],
    *,
    required_only: bool = False,
) -> list[str]:
    names: list[str] = []
    for ingredient in recipe.get("ingredients") or []:
        if required_only and ingredient.get("optional", False):
            continue
        name = str(ingredient.get("canonical_name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _numeric_value(recipe: dict[str, Any], field: str) -> float | None:
    if field == "cook_time":
        value = recipe.get("cook_time")
    else:
        value = (recipe.get("nutrition") or {}).get(field)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def build_feature_metadata(recipes: list[dict[str, Any]]) -> FeatureMetadata:
    if not recipes:
        raise ValueError("레시피 데이터셋이 비어 있습니다.")

    ingredient_vocab = sorted(
        {
            name
            for recipe in recipes
            for name in recipe_ingredient_names(recipe)
        }
    )
    category_vocab = sorted(
        {
            str(recipe.get("category") or "기타").strip() or "기타"
            for recipe in recipes
        }
    )
    recipe_ids = [recipe_id(recipe) for recipe in recipes]
    if len(set(recipe_ids)) != len(recipe_ids):
        raise ValueError("source_recipe_id가 중복된 레시피가 있습니다.")

    numeric_means: list[float] = []
    numeric_stds: list[float] = []
    for field in NUMERIC_FIELDS:
        values = [
            math.log1p(value)
            for recipe in recipes
            if (value := _numeric_value(recipe, field)) is not None
        ]
        if not values:
            numeric_means.append(0.0)
            numeric_stds.append(1.0)
            continue
        numeric_means.append(float(np.mean(values)))
        std = float(np.std(values))
        numeric_stds.append(std if std >= 1e-6 else 1.0)

    return FeatureMetadata(
        schema_version=MODEL_SCHEMA_VERSION,
        ingredient_vocab=ingredient_vocab,
        category_vocab=category_vocab,
        recipe_ids=recipe_ids,
        numeric_means=numeric_means,
        numeric_stds=numeric_stds,
        goal_names=list(GOAL_NAMES),
    )


def infer_goal_names(
    health_condition: str | None,
    health_goal: str | None,
) -> set[str]:
    value = f"{health_condition or ''} {health_goal or ''}".lower().replace(" ", "")
    goals: set[str] = set()
    if any(keyword in value for keyword in ("당뇨", "혈당", "diabetes")):
        goals.add("DIABETES_MANAGEMENT")
    if any(
        keyword in value
        for keyword in ("다이어트", "감량", "체중", "저열량", "weightloss")
    ):
        goals.add("WEIGHT_LOSS")
    if any(keyword in value for keyword in ("고혈압", "저염", "나트륨", "lowsodium")):
        goals.add("LOW_SODIUM")
    return goals or {"GENERAL"}


def eligible_goal_names(recipe: dict[str, Any]) -> list[str]:
    goals = ["GENERAL"]
    calories = _numeric_value(recipe, "calories_kcal")
    carbohydrate = _numeric_value(recipe, "carbohydrate_g")
    sodium = _numeric_value(recipe, "sodium_mg")
    ingredient_names = set(recipe_ingredient_names(recipe))

    if calories is not None and calories <= 500:
        goals.append("WEIGHT_LOSS")
    if (
        carbohydrate is not None
        and carbohydrate <= 45
        and "설탕" not in ingredient_names
    ):
        goals.append("DIABETES_MANAGEMENT")
    if sodium is not None and sodium <= 700:
        goals.append("LOW_SODIUM")
    return goals


def build_query_features(
    metadata: FeatureMetadata,
    *,
    inventory_names: Iterable[str],
    urgency_by_name: dict[str, float] | None = None,
    goal_names: Iterable[str] | None = None,
) -> np.ndarray:
    ingredient_index = {
        name: index for index, name in enumerate(metadata.ingredient_vocab)
    }
    presence = np.zeros(len(metadata.ingredient_vocab), dtype=np.float32)
    urgency = np.zeros(len(metadata.ingredient_vocab), dtype=np.float32)
    urgency_values = urgency_by_name or {}
    for raw_name in inventory_names:
        name = str(raw_name).strip()
        index = ingredient_index.get(name)
        if index is None:
            continue
        presence[index] = 1.0
        urgency[index] = float(np.clip(urgency_values.get(name, 0.0), 0.0, 1.0))

    goal_index = {name: index for index, name in enumerate(metadata.goal_names)}
    goal_vector = np.zeros(len(metadata.goal_names), dtype=np.float32)
    selected_goals = {str(value) for value in goal_names or ["GENERAL"]}
    for goal in selected_goals:
        index = goal_index.get(goal)
        if index is not None:
            goal_vector[index] = 1.0
    if not np.any(goal_vector) and "GENERAL" in goal_index:
        goal_vector[goal_index["GENERAL"]] = 1.0

    return np.concatenate((presence, urgency, goal_vector)).astype(np.float32)


def build_recipe_features(
    metadata: FeatureMetadata,
    recipe: dict[str, Any],
) -> np.ndarray:
    ingredient_index = {
        name: index for index, name in enumerate(metadata.ingredient_vocab)
    }
    ingredients = np.zeros(len(metadata.ingredient_vocab), dtype=np.float32)
    for ingredient in recipe.get("ingredients") or []:
        name = str(ingredient.get("canonical_name") or "").strip()
        index = ingredient_index.get(name)
        if index is not None:
            ingredients[index] = 0.5 if ingredient.get("optional", False) else 1.0

    numeric_values = np.zeros(len(NUMERIC_FIELDS), dtype=np.float32)
    numeric_mask = np.zeros(len(NUMERIC_FIELDS), dtype=np.float32)
    for index, field in enumerate(NUMERIC_FIELDS):
        value = _numeric_value(recipe, field)
        if value is None:
            continue
        numeric_values[index] = (
            math.log1p(value) - metadata.numeric_means[index]
        ) / metadata.numeric_stds[index]
        numeric_mask[index] = 1.0

    category_index = {
        name: index for index, name in enumerate(metadata.category_vocab)
    }
    category = np.zeros(len(metadata.category_vocab), dtype=np.float32)
    category_name = str(recipe.get("category") or "기타").strip() or "기타"
    if category_name in category_index:
        category[category_index[category_name]] = 1.0

    return np.concatenate(
        (ingredients, numeric_values, numeric_mask, category)
    ).astype(np.float32)


def urgency_from_days(days_remaining: int | None) -> float:
    if days_remaining is None or days_remaining < 0:
        return 0.0
    schedule = {
        0: 1.0,
        1: 0.95,
        2: 0.90,
        3: 0.80,
        4: 0.65,
        5: 0.55,
        6: 0.45,
        7: 0.35,
    }
    if days_remaining in schedule:
        return schedule[days_remaining]
    return 0.15 if days_remaining <= 14 else 0.0
