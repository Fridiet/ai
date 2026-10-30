from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import torch

from .features import (
    FeatureMetadata,
    build_query_features,
    infer_goal_names,
    urgency_from_days,
)
from .trainer import build_recipe_feature_matrix
from .two_tower import HybridTwoTower, TwoTowerConfig
from .preferences import build_taste_profile
from .dataset_identity import recipe_dataset_hash


logger = logging.getLogger(__name__)
DEFAULT_MODEL_NAME = "two_tower_pytorch_v1"
DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[1] / "models"
DEFAULT_RECIPES_PATH = Path(__file__).resolve().parents[1] / "data" / "recipes_ko.json"


@dataclass(frozen=True)
class RecipeModelScore:
    similarity: float
    percentile_score: float
    context_similarity: float | None = None
    taste_similarity: float | None = None


@dataclass(frozen=True)
class TwoTowerPrediction:
    model_version: str
    scores_by_recipe_id: dict[str, RecipeModelScore]
    personalization_weight: float = 0.0
    positive_event_count: int = 0


def _as_date(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


class TwoTowerRecommender:
    def __init__(
        self,
        *,
        model: HybridTwoTower,
        metadata: FeatureMetadata,
        recipes: list[dict[str, Any]],
        model_version: str,
    ) -> None:
        self.model = model
        self.metadata = metadata
        self.recipes = recipes
        self.model_version = model_version
        self.model.eval()

        recipe_features = build_recipe_feature_matrix(recipes, metadata)
        recipe_indices = np.arange(len(recipes), dtype=np.int64)
        with torch.no_grad():
            self.recipe_vectors = self.model.recipe_tower(
                torch.from_numpy(recipe_features),
                torch.from_numpy(recipe_indices),
            ).detach().cpu().numpy()

    @classmethod
    def load(
        cls,
        *,
        model_dir: Path = DEFAULT_MODEL_DIR,
        recipes_path: Path = DEFAULT_RECIPES_PATH,
        model_name: str = DEFAULT_MODEL_NAME,
    ) -> "TwoTowerRecommender":
        metadata_path = model_dir / f"{model_name}.metadata.json"
        params_path = model_dir / f"{model_name}.pt"
        bundle = json.loads(metadata_path.read_text(encoding="utf-8"))
        if bundle.get("framework") != "pytorch":
            raise ValueError("PyTorch로 학습된 Two-Tower 모델이 아닙니다.")
        metadata = FeatureMetadata.from_dict(bundle["feature_metadata"])
        recipes = json.loads(recipes_path.read_text(encoding="utf-8"))
        if bundle.get("recipe_dataset_hash") != recipe_dataset_hash(recipes):
            raise ValueError("레시피 재료가 학습 시점과 다릅니다. 새 데이터로 재학습해야 합니다.")
        current_recipe_ids = [str(recipe["source_recipe_id"]) for recipe in recipes]
        if current_recipe_ids != metadata.recipe_ids:
            raise ValueError(
                "Two-Tower 모델과 recipes_ko.json의 레시피 순서가 다릅니다. "
                "모델을 다시 학습하세요."
            )

        model = HybridTwoTower(TwoTowerConfig.from_dict(bundle["model_config"]))
        state_dict = torch.load(params_path, map_location="cpu", weights_only=True)
        model.load_state_dict(state_dict)
        return cls(
            model=model,
            metadata=metadata,
            recipes=recipes,
            model_version=str(bundle["model_version"]),
        )

    def predict(
        self,
        *,
        inventory_by_name: dict[str, list[dict[str, Any]]],
        health_condition: str | None,
        health_goal: str | None,
        today: date,
        recent_feedback: list[dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> TwoTowerPrediction:
        urgency_by_name: dict[str, float] = {}
        for name, snapshots in inventory_by_name.items():
            consume_dates = [
                parsed
                for snapshot in snapshots
                if (parsed := _as_date(snapshot.get("consume_by_date"))) is not None
            ]
            if consume_dates:
                days_remaining = (min(consume_dates) - today).days
                urgency_by_name[name] = urgency_from_days(days_remaining)

        query_features = build_query_features(
            self.metadata,
            inventory_names=inventory_by_name.keys(),
            urgency_by_name=urgency_by_name,
            goal_names=infer_goal_names(health_condition, health_goal),
        )
        with torch.no_grad():
            query_vector = self.model.query_tower(
                torch.from_numpy(query_features.reshape(1, -1))
            ).detach().cpu().numpy()[0]
        context_similarities = self.recipe_vectors @ query_vector
        profile = build_taste_profile(
            self.recipes, self.recipe_vectors, recent_feedback or [], now or datetime.now()
        )
        taste_similarities = None
        if profile.vector is not None:
            taste_similarities = self.recipe_vectors @ profile.vector
            query_vector = (1.0 - profile.weight) * query_vector + profile.weight * profile.vector
            query_vector = query_vector / max(float(np.linalg.norm(query_vector)), 1e-8)
        similarities = self.recipe_vectors @ query_vector

        order = np.argsort(-similarities, kind="stable")
        ranks = np.empty(len(order), dtype=np.int64)
        ranks[order] = np.arange(len(order), dtype=np.int64)
        denominator = max(len(order) - 1, 1)
        percentile_scores = 100.0 * (1.0 - ranks / denominator)

        return TwoTowerPrediction(
            model_version=self.model_version,
            personalization_weight=profile.weight,
            positive_event_count=profile.event_count,
            scores_by_recipe_id={
                recipe_id: RecipeModelScore(
                    similarity=float(similarities[index]),
                    percentile_score=float(percentile_scores[index]),
                    context_similarity=float(context_similarities[index]),
                    taste_similarity=(float(taste_similarities[index]) if taste_similarities is not None else None),
                )
                for index, recipe_id in enumerate(self.metadata.recipe_ids)
            },
        )


@lru_cache(maxsize=1)
def get_two_tower_recommender() -> TwoTowerRecommender | None:
    enabled = os.getenv("TWO_TOWER_ENABLED", "true").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return None

    model_dir = Path(os.getenv("TWO_TOWER_MODEL_DIR", str(DEFAULT_MODEL_DIR)))
    try:
        recommender = TwoTowerRecommender.load(model_dir=model_dir)
        logger.info("Two-Tower 추천 모델을 불러왔습니다: %s", recommender.model_version)
        return recommender
    except (FileNotFoundError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        logger.warning("Two-Tower 모델을 사용할 수 없어 규칙 기반 추천으로 전환합니다: %s", exc)
        return None
