"""Aggregate positive feedback in the trained recipe embedding space.

This is inference-time personalization, not online parameter training.
Legacy feedback has names only: ambiguous/unknown names are deliberately ignored.
"""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any
from collections import Counter

import numpy as np


def ingredient_taste_scores(recipes, feedback, now):
    """IDF-weighted content reranker; no account IDs or hand-picked food groups.

    Optional ingredients do not define preference. Frequent staples receive less
    influence. Reuse the same deduplication, recency and cold-start safeguards.
    """
    if not any(str(event.get('eventType', '')).upper() in {'SAVED', 'COOKED'} for event in feedback):
        return {}, 0.0
    names = [{i['canonical_name'] for i in r.get('ingredients', [])
              if not i.get('optional', False)} for r in recipes]
    counts = Counter(name for row in names for name in row)
    vocabulary = {name: index for index, name in enumerate(sorted(counts))}
    matrix = np.zeros((len(recipes), len(vocabulary)), dtype=np.float32)
    for index, row in enumerate(names):
        for name in row:
            matrix[index, vocabulary[name]] = np.log(1 + len(recipes) / counts[name])
    matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-8)
    profile = build_taste_profile(recipes, matrix, feedback, now)
    if profile.vector is None:
        return {}, 0.0
    scores = matrix @ profile.vector
    return {f"{r.get('source') or 'LOCAL'}:{r['source_recipe_id']}": float(scores[i])
            for i, r in enumerate(recipes)}, profile.weight


@dataclass(frozen=True)
class TasteProfile:
    vector: np.ndarray | None
    event_count: int
    weight: float


def _utc(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    # Existing Spring LocalDateTime feedback uses the application's Korea timezone.
    if parsed.tzinfo is None:
        from datetime import timedelta
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=9)))
    return parsed.astimezone(timezone.utc)


def build_taste_profile(
    recipes: list[dict[str, Any]],
    recipe_vectors: np.ndarray,
    feedback: list[dict[str, Any]],
    now: datetime,
) -> TasteProfile:
    indices: dict[str, list[int]] = {}
    keys = {}
    for index, recipe in enumerate(recipes):
        indices.setdefault(recipe["recipe_name"].strip(), []).append(index)
        if recipe.get("source_recipe_id"):
            keys[f"{recipe.get('source') or 'LOCAL'}:{recipe['source_recipe_id']}"] = index
    weights: dict[tuple[int, str, str], float] = {}
    now_utc = _utc(now)
    for event in feedback:
        kind = str(event.get("eventType", "")).upper()
        if kind not in {"SAVED", "COOKED"}:
            continue
        key = event.get("recipeKey")
        matches = ([keys[key]] if key in keys else []) if key else indices.get(str(event.get("recipeName", "")).strip(), [])
        if len(matches) != 1:
            continue
        try:
            occurred = _utc(event["occurredAt"])
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        age = (now_utc - occurred).total_seconds() / 86400
        if age < 0 or age > 30:
            continue
        # Saving repeatedly must not manufacture a preference. Cooking counts once/day.
        bucket = occurred.astimezone(timezone(timedelta(hours=9))).date().isoformat() if kind == "COOKED" else "once"
        key = (matches[0], kind, bucket)
        strength = (3.0 if kind == "COOKED" else 1.0) * 2 ** (-age / 14.0)
        weights[key] = max(weights.get(key, 0.0), strength)
    if not weights:
        return TasteProfile(None, 0, 0.0)
    vector = sum(recipe_vectors[index] * strength for (index, _, _), strength in weights.items())
    norm = float(np.linalg.norm(vector))
    if norm < 1e-8:
        return TasteProfile(None, 0, 0.0)
    # Sustained evidence, not a burst of saves, can outweigh shared inventory.
    cooked_days = {bucket for (_, kind, bucket) in weights if kind == 'COOKED'}
    confidence = min(1.0, sum(weights.values()) / 60.0)
    confidence *= min(1.0, max(1, len(cooked_days)) / 7.0)
    return TasteProfile((vector / norm).astype(np.float32), len(weights), 0.65 * confidence)
