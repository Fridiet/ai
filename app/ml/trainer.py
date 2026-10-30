from __future__ import annotations

import copy
import random
from collections import defaultdict
from typing import Any

import numpy as np
import torch
from torch.nn import functional as F

from .features import (
    FeatureMetadata,
    build_query_features,
    build_recipe_features,
)
from .two_tower import HybridTwoTower, TwoTowerConfig


def build_recipe_feature_matrix(
    recipes: list[dict[str, Any]],
    metadata: FeatureMetadata,
) -> np.ndarray:
    return np.stack(
        [build_recipe_features(metadata, recipe) for recipe in recipes]
    ).astype(np.float32)


def build_query_feature_matrix(
    samples: list[dict[str, Any]],
    metadata: FeatureMetadata,
) -> np.ndarray:
    return np.stack(
        [
            build_query_features(
                metadata,
                inventory_names=sample["inventory"],
                urgency_by_name=sample.get("urgency") or {},
                goal_names=sample.get("goals") or ["GENERAL"],
            )
            for sample in samples
        ]
    ).astype(np.float32)


def _metrics_from_ranks(ranks: list[int]) -> dict[str, float]:
    if not ranks:
        raise ValueError("평가할 추천 순위가 없습니다.")
    rank_array = np.asarray(ranks, dtype=np.float64)

    def recall_at(k: int) -> float:
        return float(np.mean(rank_array <= k))

    def ndcg_at(k: int) -> float:
        gains = np.where(
            rank_array <= k,
            1.0 / np.log2(rank_array + 1.0),
            0.0,
        )
        return float(np.mean(gains))

    return {
        "query_count": float(len(ranks)),
        "recall_at_1": recall_at(1),
        "recall_at_5": recall_at(5),
        "recall_at_10": recall_at(10),
        "ndcg_at_5": ndcg_at(5),
        "ndcg_at_10": ndcg_at(10),
        "mrr": float(np.mean(1.0 / rank_array)),
        "mean_rank": float(np.mean(rank_array)),
    }


def _target_ranks(scores: np.ndarray, target_indices: np.ndarray) -> list[int]:
    ranks: list[int] = []
    for row_index, target_index in enumerate(target_indices):
        order = np.argsort(-scores[row_index], kind="stable")
        rank = int(np.flatnonzero(order == target_index)[0]) + 1
        ranks.append(rank)
    return ranks


@torch.no_grad()
def evaluate_model(
    model: HybridTwoTower,
    *,
    samples: list[dict[str, Any]],
    recipes: list[dict[str, Any]],
    metadata: FeatureMetadata,
    batch_size: int = 256,
) -> dict[str, float]:
    if not samples:
        raise ValueError("평가 샘플이 없습니다.")

    model.eval()
    device = next(model.parameters()).device
    recipe_feature_matrix = build_recipe_feature_matrix(recipes, metadata)
    recipe_indices = np.arange(len(recipes), dtype=np.int64)
    recipe_vectors = model.recipe_tower(
        torch.from_numpy(recipe_feature_matrix).to(device),
        torch.from_numpy(recipe_indices).to(device),
    ).detach().cpu().numpy()

    query_feature_matrix = build_query_feature_matrix(samples, metadata)
    target_indices = np.asarray(
        [int(sample["recipe_index"]) for sample in samples],
        dtype=np.int64,
    )
    ranks: list[int] = []
    for start in range(0, len(samples), batch_size):
        end = start + batch_size
        query_vectors = model.query_tower(
            torch.from_numpy(query_feature_matrix[start:end]).to(device)
        ).detach().cpu().numpy()
        scores = query_vectors @ recipe_vectors.T
        ranks.extend(_target_ranks(scores, target_indices[start:end]))
    return _metrics_from_ranks(ranks)


def evaluate_overlap_baseline(
    *,
    samples: list[dict[str, Any]],
    recipes: list[dict[str, Any]],
    metadata: FeatureMetadata,
    batch_size: int = 256,
) -> dict[str, float]:
    if not samples:
        raise ValueError("평가 샘플이 없습니다.")

    recipe_features = build_recipe_feature_matrix(recipes, metadata)
    ingredient_count = len(metadata.ingredient_vocab)
    recipe_presence = (recipe_features[:, :ingredient_count] > 0).astype(np.float32)
    recipe_sizes = np.maximum(recipe_presence.sum(axis=1), 1.0)
    query_features = build_query_feature_matrix(samples, metadata)
    target_indices = np.asarray(
        [int(sample["recipe_index"]) for sample in samples],
        dtype=np.int64,
    )

    ranks: list[int] = []
    for start in range(0, len(samples), batch_size):
        end = start + batch_size
        presence = query_features[start:end, :ingredient_count]
        urgency = query_features[
            start:end,
            ingredient_count : ingredient_count * 2,
        ]
        intersections = presence @ recipe_presence.T
        query_sizes = np.maximum(presence.sum(axis=1, keepdims=True), 1.0)
        unions = np.maximum(
            query_sizes + recipe_sizes.reshape(1, -1) - intersections,
            1.0,
        )
        coverage = intersections / recipe_sizes.reshape(1, -1)
        jaccard = intersections / unions
        expiry_match = (urgency @ recipe_presence.T) / recipe_sizes.reshape(1, -1)
        scores = 0.55 * coverage + 0.25 * jaccard + 0.20 * expiry_match
        ranks.extend(_target_ranks(scores, target_indices[start:end]))
    return _metrics_from_ranks(ranks)


def evaluate_model_by_scenario(
    model: HybridTwoTower,
    *,
    samples: list[dict[str, Any]],
    recipes: list[dict[str, Any]],
    metadata: FeatureMetadata,
    batch_size: int = 256,
) -> dict[str, dict[str, float]]:
    scenarios = sorted({str(sample.get("scenario") or "UNKNOWN") for sample in samples})
    return {
        scenario: evaluate_model(
            model,
            samples=[
                sample
                for sample in samples
                if str(sample.get("scenario") or "UNKNOWN") == scenario
            ],
            recipes=recipes,
            metadata=metadata,
            batch_size=batch_size,
        )
        for scenario in scenarios
    }


def train_model(
    *,
    recipes: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    metadata: FeatureMetadata,
    epochs: int = 40,
    batch_size: int = 64,
    learning_rate: float = 1e-3,
    seed: int = 42,
    patience: int = 8,
    hard_negative_weight: float = 0.5,
) -> tuple[HybridTwoTower, dict[str, Any]]:
    if epochs < 1:
        raise ValueError("epochs는 1 이상이어야 합니다.")
    if batch_size < 2:
        raise ValueError("batch_size는 2 이상이어야 합니다.")
    if hard_negative_weight < 0:
        raise ValueError("hard_negative_weight는 0 이상이어야 합니다.")

    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    np.random.seed(seed)
    random.seed(seed)

    config = TwoTowerConfig(
        query_input_size=metadata.query_input_size,
        recipe_input_size=metadata.recipe_input_size,
        recipe_count=len(recipes),
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = HybridTwoTower(config).to(device)
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=learning_rate,
        weight_decay=1e-5,
    )
    recipe_feature_matrix = build_recipe_feature_matrix(recipes, metadata)

    train_by_recipe: dict[int, list[dict[str, Any]]] = defaultdict(list)
    validation_samples: list[dict[str, Any]] = []
    for sample in samples:
        if sample["split"] == "train":
            train_by_recipe[int(sample["recipe_index"])].append(sample)
        elif sample["split"] == "validation":
            validation_samples.append(sample)

    expected_recipe_indices = set(range(len(recipes)))
    if set(train_by_recipe) != expected_recipe_indices:
        raise ValueError("모든 레시피에 train context가 있어야 합니다.")
    if not validation_samples:
        raise ValueError("validation context가 없습니다.")

    best_state: dict[str, torch.Tensor] | None = None
    best_ndcg = -1.0
    epochs_without_improvement = 0
    history: list[dict[str, Any]] = []

    for epoch in range(1, epochs + 1):
        epoch_rng = random.Random(seed + epoch)
        selected_samples = [
            epoch_rng.choice(train_by_recipe[index])
            for index in range(len(recipes))
        ]
        epoch_rng.shuffle(selected_samples)
        model.train()
        losses: list[float] = []
        in_batch_losses: list[float] = []
        hard_negative_losses: list[float] = []

        for start in range(0, len(selected_samples), batch_size):
            batch = selected_samples[start : start + batch_size]
            if len(batch) < 2:
                continue
            target_indices = np.asarray(
                [int(sample["recipe_index"]) for sample in batch],
                dtype=np.int64,
            )
            query_features = build_query_feature_matrix(batch, metadata)
            recipe_features = recipe_feature_matrix[target_indices]

            query_vectors, positive_vectors = model(
                torch.from_numpy(query_features).to(device),
                torch.from_numpy(recipe_features).to(device),
                torch.from_numpy(target_indices).to(device),
            )
            logits = torch.matmul(
                query_vectors, positive_vectors.transpose(0, 1)
            ) / float(model.config.temperature)
            labels = torch.arange(len(batch), dtype=torch.long, device=device)
            in_batch_loss = F.cross_entropy(logits, labels)
            loss = in_batch_loss

            negative_count = min(
                (len(sample.get("hard_negative_indices") or []) for sample in batch),
                default=0,
            )
            if hard_negative_weight > 0 and negative_count:
                negative_indices = np.asarray(
                    [
                        sample["hard_negative_indices"][:negative_count]
                        for sample in batch
                    ],
                    dtype=np.int64,
                )
                flat_negative_indices = negative_indices.reshape(-1)
                negative_features = recipe_feature_matrix[flat_negative_indices]
                negative_vectors = model.recipe_tower(
                    torch.from_numpy(negative_features).to(device),
                    torch.from_numpy(flat_negative_indices).to(device),
                ).reshape(len(batch), negative_count, -1)
                positive_scores = torch.sum(
                    query_vectors * positive_vectors,
                    dim=-1,
                    keepdim=True,
                )
                negative_scores = torch.sum(
                    query_vectors.unsqueeze(1) * negative_vectors,
                    dim=-1,
                )
                hard_logits = torch.cat(
                    (positive_scores, negative_scores), dim=1
                ) / float(model.config.temperature)
                hard_labels = torch.zeros(
                    len(batch), dtype=torch.long, device=device
                )
                hard_negative_loss = F.cross_entropy(hard_logits, hard_labels)
                loss = loss + hard_negative_weight * hard_negative_loss
                hard_negative_losses.append(float(hard_negative_loss.item()))

            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
            losses.append(float(loss.item()))
            in_batch_losses.append(float(in_batch_loss.item()))

        validation_metrics = evaluate_model(
            model,
            samples=validation_samples,
            recipes=recipes,
            metadata=metadata,
            batch_size=max(batch_size, 128),
        )
        epoch_record = {
            "epoch": epoch,
            "training_loss": float(np.mean(losses)),
            "in_batch_loss": float(np.mean(in_batch_losses)),
            "hard_negative_loss": (
                float(np.mean(hard_negative_losses))
                if hard_negative_losses
                else None
            ),
            "validation_ndcg_at_5": validation_metrics["ndcg_at_5"],
            "validation_recall_at_5": validation_metrics["recall_at_5"],
        }
        history.append(epoch_record)

        if validation_metrics["ndcg_at_5"] > best_ndcg + 1e-6:
            best_ndcg = validation_metrics["ndcg_at_5"]
            best_state = copy.deepcopy(model.state_dict())
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= patience:
                break

    if best_state is None:
        raise RuntimeError("학습된 모델 상태를 저장하지 못했습니다.")
    model.load_state_dict(best_state)
    model.eval()
    return model, {
        "epochs_requested": epochs,
        "epochs_completed": len(history),
        "best_validation_ndcg_at_5": best_ndcg,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "hard_negative_weight": hard_negative_weight,
        "seed": seed,
        "history": history,
    }
