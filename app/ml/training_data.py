from __future__ import annotations

import math
import random
from collections import Counter
from typing import Any

from .features import (
    build_feature_metadata,
    eligible_goal_names,
    recipe_id,
    recipe_ingredient_names,
)


DEFAULT_CONTEXTS_PER_RECIPE = 32
DEFAULT_HARD_NEGATIVE_COUNT = 4
SYNTHETIC_SOURCE = "constraint_persona_v2"
SCENARIO_NAMES = (
    "SPARSE_PANTRY",
    "BALANCED_PANTRY",
    "WELL_STOCKED",
    "EXPIRY_RESCUE",
)


def _kept_count_range(required_count: int, scenario: str) -> tuple[int, int]:
    if scenario == "SPARSE_PANTRY":
        return 1, max(1, math.ceil(required_count * 0.5))
    if scenario == "BALANCED_PANTRY":
        return max(1, math.ceil(required_count * 0.5)), max(
            1, math.ceil(required_count * 0.8)
        )
    if scenario == "WELL_STOCKED":
        return max(1, math.ceil(required_count * 0.75)), required_count
    return max(1, math.ceil(required_count * 0.4)), required_count


def _select_goal(recipe: dict[str, Any], rng: random.Random) -> str:
    specialized = [
        goal for goal in eligible_goal_names(recipe) if goal != "GENERAL"
    ]
    if specialized and rng.random() < 0.6:
        return rng.choice(specialized)
    return "GENERAL"


def _build_hard_negative_pools(
    recipes: list[dict[str, Any]],
    *,
    pool_size: int = 24,
) -> list[dict[str, list[int]]]:
    ingredient_sets = [set(recipe_ingredient_names(recipe)) for recipe in recipes]
    categories = [
        str(recipe.get("category") or "기타").strip() or "기타"
        for recipe in recipes
    ]
    eligible_goals = [set(eligible_goal_names(recipe)) for recipe in recipes]
    pools: list[dict[str, list[int]]] = []

    for target_index, target_ingredients in enumerate(ingredient_sets):
        by_goal: dict[str, list[int]] = {}
        for goal in (
            "GENERAL",
            "WEIGHT_LOSS",
            "DIABETES_MANAGEMENT",
            "LOW_SODIUM",
        ):
            ranked: list[tuple[float, str, int]] = []
            for candidate_index, candidate_ingredients in enumerate(ingredient_sets):
                if candidate_index == target_index:
                    continue
                intersection = len(target_ingredients & candidate_ingredients)
                union = max(1, len(target_ingredients | candidate_ingredients))
                candidate_size = max(1, len(candidate_ingredients))
                score = 0.55 * (intersection / candidate_size)
                score += 0.35 * (intersection / union)
                if categories[candidate_index] == categories[target_index]:
                    score += 0.10
                if goal != "GENERAL" and goal in eligible_goals[candidate_index]:
                    score += 0.08
                ranked.append(
                    (-score, recipe_id(recipes[candidate_index]), candidate_index)
                )
            ranked.sort()
            compatible = [
                index
                for _, _, index in ranked
                if goal == "GENERAL" or goal in eligible_goals[index]
            ]
            if len(compatible) < pool_size:
                compatible.extend(
                    index
                    for _, _, index in ranked
                    if index not in compatible
                )
            by_goal[goal] = compatible[:pool_size]
        pools.append(by_goal)
    return pools


def _generate_context(
    recipe: dict[str, Any],
    *,
    recipe_index: int,
    context_index: int,
    ingredient_vocab: list[str],
    hard_negative_pool: dict[str, list[int]],
    hard_negative_count: int,
    rng: random.Random,
) -> dict[str, Any]:
    required = recipe_ingredient_names(recipe, required_only=True)
    all_recipe_ingredients = recipe_ingredient_names(recipe)
    if not required:
        required = all_recipe_ingredients
    if not required:
        raise ValueError(f"재료가 없는 레시피입니다: {recipe_id(recipe)}")

    primary = [
        str(ingredient.get("canonical_name") or "").strip()
        for ingredient in recipe.get("ingredients") or []
        if ingredient.get("primary", False)
        and str(ingredient.get("canonical_name") or "").strip() in required
    ]
    primary = list(dict.fromkeys(primary))

    scenario = SCENARIO_NAMES[context_index % len(SCENARIO_NAMES)]
    minimum_kept, maximum_kept = _kept_count_range(len(required), scenario)
    kept_count = rng.randint(minimum_kept, maximum_kept)
    inventory = set(primary)
    remaining_required = [name for name in required if name not in inventory]
    remaining_count = min(
        len(remaining_required),
        max(0, kept_count - len(inventory)),
    )
    if remaining_count:
        inventory.update(rng.sample(remaining_required, remaining_count))

    optional = [name for name in all_recipe_ingredients if name not in required]
    optional_probability = 0.65 if scenario == "WELL_STOCKED" else 0.30
    for name in optional:
        if rng.random() < optional_probability:
            inventory.add(name)

    distractors = [
        name for name in ingredient_vocab if name not in all_recipe_ingredients
    ]
    distractor_limits = {
        "SPARSE_PANTRY": 1,
        "BALANCED_PANTRY": 3,
        "WELL_STOCKED": 6,
        "EXPIRY_RESCUE": 3,
    }
    distractor_count = rng.randint(
        0, min(distractor_limits[scenario], len(distractors))
    )
    if distractor_count:
        inventory.update(rng.sample(distractors, distractor_count))

    urgency_choices = (0.0, 0.15, 0.35, 0.55, 0.8, 0.95, 1.0)
    urgency = {name: rng.choice(urgency_choices) for name in sorted(inventory)}
    owned_recipe_ingredients = sorted(inventory.intersection(all_recipe_ingredients))
    if owned_recipe_ingredients:
        urgent_probability = 1.0 if scenario == "EXPIRY_RESCUE" else 0.55
        if rng.random() < urgent_probability:
            urgent_count = 1
            if scenario == "EXPIRY_RESCUE" and len(owned_recipe_ingredients) > 1:
                urgent_count = rng.randint(1, min(2, len(owned_recipe_ingredients)))
            for urgent_name in rng.sample(owned_recipe_ingredients, urgent_count):
                urgency[urgent_name] = rng.choice((0.8, 0.95, 1.0))

    goal = _select_goal(recipe, rng)
    negative_pool = list(hard_negative_pool.get(goal) or hard_negative_pool["GENERAL"])
    selected_negative_count = min(hard_negative_count, len(negative_pool))
    hard_negative_indices = (
        rng.sample(negative_pool, selected_negative_count)
        if selected_negative_count
        else []
    )
    persona_number = (recipe_index * 31 + context_index * 17) % 256

    return {
        "sample_id": f"syn-{recipe_id(recipe)}-{context_index:03d}",
        "source": SYNTHETIC_SOURCE,
        "scenario": scenario,
        "persona_id": f"persona-{persona_number:03d}",
        "inventory": sorted(inventory),
        "urgency": urgency,
        "goals": [goal],
        "hard_negative_indices": hard_negative_indices,
    }


def _validate_and_summarize(
    samples: list[dict[str, Any]],
    *,
    recipe_count: int,
) -> dict[str, Any]:
    sample_ids: set[str] = set()
    split_counts: Counter[str] = Counter()
    scenario_counts: Counter[str] = Counter()
    goal_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()

    for sample in samples:
        sample_id = str(sample["sample_id"])
        if sample_id in sample_ids:
            raise ValueError(f"중복된 합성 sample_id입니다: {sample_id}")
        sample_ids.add(sample_id)
        target_index = int(sample["recipe_index"])
        if not 0 <= target_index < recipe_count:
            raise ValueError(f"잘못된 recipe_index입니다: {target_index}")
        if not sample.get("inventory"):
            raise ValueError(f"냉장고 재료가 없는 sample입니다: {sample_id}")
        negatives = [int(value) for value in sample.get("hard_negative_indices") or []]
        if target_index in negatives or len(negatives) != len(set(negatives)):
            raise ValueError(f"잘못된 hard negative입니다: {sample_id}")
        if any(index < 0 or index >= recipe_count for index in negatives):
            raise ValueError(f"범위를 벗어난 hard negative입니다: {sample_id}")

        split_counts[str(sample["split"])] += 1
        scenario_counts[str(sample["scenario"])] += 1
        source_counts[str(sample["source"])] += 1
        for goal in sample.get("goals") or ["GENERAL"]:
            goal_counts[str(goal)] += 1

    if set(split_counts) != {"train", "validation", "test"}:
        raise ValueError(f"데이터 분할이 올바르지 않습니다: {dict(split_counts)}")

    return {
        "total_samples": len(samples),
        "split_counts": dict(sorted(split_counts.items())),
        "scenario_counts": dict(sorted(scenario_counts.items())),
        "goal_counts": dict(sorted(goal_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
    }


def build_training_payload(
    recipes: list[dict[str, Any]],
    *,
    contexts_per_recipe: int = DEFAULT_CONTEXTS_PER_RECIPE,
    hard_negative_count: int = DEFAULT_HARD_NEGATIVE_COUNT,
    seed: int = 42,
) -> dict[str, Any]:
    if contexts_per_recipe < 3:
        raise ValueError("레시피마다 train/validation/test용으로 최소 3개 context가 필요합니다.")
    if hard_negative_count < 0:
        raise ValueError("hard_negative_count는 0 이상이어야 합니다.")

    metadata = build_feature_metadata(recipes)
    hard_negative_pools = _build_hard_negative_pools(recipes)
    holdout_contexts_per_split = 4 if contexts_per_recipe >= 10 else 1
    train_context_count = contexts_per_recipe - holdout_contexts_per_split * 2
    samples: list[dict[str, Any]] = []
    for recipe_index, recipe in enumerate(recipes):
        local_seed = seed * 1_000_003 + recipe_index
        rng = random.Random(local_seed)
        for context_index in range(contexts_per_recipe):
            if context_index < train_context_count:
                split = "train"
            elif context_index < train_context_count + holdout_contexts_per_split:
                split = "validation"
            else:
                split = "test"
            context = _generate_context(
                recipe,
                recipe_index=recipe_index,
                context_index=context_index,
                ingredient_vocab=metadata.ingredient_vocab,
                hard_negative_pool=hard_negative_pools[recipe_index],
                hard_negative_count=hard_negative_count,
                rng=rng,
            )
            samples.append(
                {
                    "recipe_index": recipe_index,
                    "recipe_id": recipe_id(recipe),
                    "split": split,
                    **context,
                }
            )

    statistics = _validate_and_summarize(samples, recipe_count=len(recipes))
    return {
        "format_version": 2,
        "source_type": "synthetic",
        "generation_strategy": SYNTHETIC_SOURCE,
        "seed": seed,
        "contexts_per_recipe": contexts_per_recipe,
        "holdout_contexts_per_split": holdout_contexts_per_split,
        "hard_negative_count": hard_negative_count,
        "metadata": metadata.to_dict(),
        "statistics": statistics,
        "samples": samples,
    }
