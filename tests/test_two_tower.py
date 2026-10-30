from __future__ import annotations

import numpy as np
import torch

from app.ml.features import (
    FeatureMetadata,
    build_feature_metadata,
    build_query_features,
    build_recipe_features,
    infer_goal_names,
)
from app.ml.training_data import build_training_payload
from app.ml.two_tower import HybridTwoTower, TwoTowerConfig


def _recipes() -> list[dict]:
    return [
        {
            "source_recipe_id": "recipe-1",
            "recipe_name": "두부 샐러드",
            "cook_time": 10,
            "category": "샐러드",
            "ingredients": [
                {
                    "canonical_name": "두부",
                    "quantity": 100,
                    "unit": "g",
                    "optional": False,
                    "primary": True,
                },
                {
                    "canonical_name": "양상추",
                    "quantity": 50,
                    "unit": "g",
                    "optional": False,
                },
            ],
            "nutrition": {
                "calories_kcal": 220,
                "carbohydrate_g": 18,
                "protein_g": 15,
                "fat_g": 8,
                "sodium_mg": 300,
            },
        },
        {
            "source_recipe_id": "recipe-2",
            "recipe_name": "닭고기 볶음",
            "cook_time": 20,
            "category": "볶음",
            "ingredients": [
                {
                    "canonical_name": "닭고기",
                    "quantity": 200,
                    "unit": "g",
                    "optional": False,
                },
                {
                    "canonical_name": "양파",
                    "quantity": 50,
                    "unit": "g",
                    "optional": False,
                },
            ],
            "nutrition": {
                "calories_kcal": 410,
                "carbohydrate_g": 30,
                "protein_g": 35,
                "fat_g": 12,
                "sodium_mg": 650,
            },
        },
    ]


def test_feature_metadata_and_vectors_have_stable_dimensions() -> None:
    recipes = _recipes()
    metadata = build_feature_metadata(recipes)
    query = build_query_features(
        metadata,
        inventory_names=["두부", "양상추"],
        urgency_by_name={"두부": 1.0},
        goal_names={"WEIGHT_LOSS"},
    )
    recipe = build_recipe_features(metadata, recipes[0])

    assert query.shape == (metadata.query_input_size,)
    assert recipe.shape == (metadata.recipe_input_size,)
    assert query.dtype == np.float32
    assert recipe.dtype == np.float32
    assert infer_goal_names("당뇨", "저염 식단") == {
        "DIABETES_MANAGEMENT",
        "LOW_SODIUM",
    }


def test_training_context_generation_is_reproducible() -> None:
    first = build_training_payload(_recipes(), contexts_per_recipe=4, seed=7)
    second = build_training_payload(_recipes(), contexts_per_recipe=4, seed=7)

    assert first == second
    assert len(first["samples"]) == 8
    assert {sample["split"] for sample in first["samples"]} == {
        "train",
        "validation",
        "test",
    }
    assert first["format_version"] == 2
    assert first["source_type"] == "synthetic"
    assert first["statistics"]["total_samples"] == 8
    for sample in first["samples"]:
        assert sample["source"] == "constraint_persona_v2"
        assert sample["scenario"] in {
            "SPARSE_PANTRY",
            "BALANCED_PANTRY",
            "WELL_STOCKED",
            "EXPIRY_RESCUE",
        }
        assert sample["recipe_index"] not in sample["hard_negative_indices"]
        assert len(sample["hard_negative_indices"]) == 1
        if sample["recipe_index"] == 0:
            assert "두부" in sample["inventory"]


def test_two_tower_outputs_normalized_embeddings() -> None:
    recipes = _recipes()
    metadata = build_feature_metadata(recipes)
    config = TwoTowerConfig(
        query_input_size=metadata.query_input_size,
        recipe_input_size=metadata.recipe_input_size,
        recipe_count=len(recipes),
        dropout=0.0,
    )
    model = HybridTwoTower(config)
    query_features = np.stack(
        [
            build_query_features(
                metadata,
                inventory_names=["두부", "양상추"],
                goal_names={"GENERAL"},
            ),
            build_query_features(
                metadata,
                inventory_names=["닭고기", "양파"],
                goal_names={"GENERAL"},
            ),
        ]
    )
    recipe_features = np.stack(
        [build_recipe_features(metadata, recipe) for recipe in recipes]
    )

    query_vectors, recipe_vectors = model(
        torch.from_numpy(query_features),
        torch.from_numpy(recipe_features),
        torch.tensor([0, 1], dtype=torch.long),
    )

    assert query_vectors.shape == (2, 64)
    assert recipe_vectors.shape == (2, 64)
    np.testing.assert_allclose(
        np.linalg.norm(query_vectors.detach().numpy(), axis=1),
        np.ones(2),
        atol=1e-5,
    )
    np.testing.assert_allclose(
        np.linalg.norm(recipe_vectors.detach().numpy(), axis=1),
        np.ones(2),
        atol=1e-5,
    )


def test_metadata_round_trip() -> None:
    metadata = build_feature_metadata(_recipes())
    restored = FeatureMetadata.from_dict(metadata.to_dict())
    assert restored == metadata
