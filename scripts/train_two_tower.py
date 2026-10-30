from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import torch

from app.ml.features import FeatureMetadata
from app.ml.trainer import (
    evaluate_model,
    evaluate_model_by_scenario,
    evaluate_overlap_baseline,
    train_model,
)


AI_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECIPES_PATH = AI_ROOT / "app" / "data" / "recipes_ko.json"
DEFAULT_DATA_PATH = AI_ROOT / "output" / "two_tower" / "training_data.json"
DEFAULT_MODEL_DIR = AI_ROOT / "app" / "models"
MODEL_NAME = "two_tower_pytorch_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fridiet Two-Tower 모델 학습")
    parser.add_argument("--recipes", type=Path, default=DEFAULT_RECIPES_PATH)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--hard-negative-weight", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def _rounded(metrics: dict[str, float]) -> dict[str, float]:
    return {key: round(value, 6) for key, value in metrics.items()}


def _rounded_groups(
    groups: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    return {name: _rounded(metrics) for name, metrics in groups.items()}


def main() -> None:
    from app.ml.dataset_identity import recipe_dataset_hash
    args = parse_args()
    if not args.data.exists():
        raise SystemExit(
            "학습 데이터가 없습니다. 먼저 "
            ".\\.venv\\Scripts\\python.exe -m scripts.build_training_data 를 실행하세요."
        )

    recipes = json.loads(args.recipes.read_text(encoding="utf-8"))
    payload = json.loads(args.data.read_text(encoding="utf-8"))
    if payload.get('recipe_dataset_hash') != recipe_dataset_hash(recipes):
        raise SystemExit('학습 데이터의 레시피 내용이 다릅니다. build_training_data를 다시 실행하세요.')
    metadata = FeatureMetadata.from_dict(payload["metadata"])
    if metadata.recipe_ids != [str(recipe["source_recipe_id"]) for recipe in recipes]:
        raise SystemExit("학습 데이터와 현재 레시피 데이터셋의 순서가 다릅니다.")

    print(
        f"학습 시작: recipes={len(recipes):,}, samples={len(payload['samples']):,}, "
        f"device={'cuda' if torch.cuda.is_available() else 'cpu'}"
    )
    model, training_summary = train_model(
        recipes=recipes,
        samples=payload["samples"],
        metadata=metadata,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        seed=args.seed,
        patience=args.patience,
        hard_negative_weight=args.hard_negative_weight,
    )

    validation_samples = [
        sample for sample in payload["samples"] if sample["split"] == "validation"
    ]
    test_samples = [
        sample for sample in payload["samples"] if sample["split"] == "test"
    ]
    validation_metrics = evaluate_model(
        model,
        samples=validation_samples,
        recipes=recipes,
        metadata=metadata,
    )
    test_metrics = evaluate_model(
        model,
        samples=test_samples,
        recipes=recipes,
        metadata=metadata,
    )
    baseline_metrics = evaluate_overlap_baseline(
        samples=test_samples,
        recipes=recipes,
        metadata=metadata,
    )

    args.model_dir.mkdir(parents=True, exist_ok=True)
    params_path = args.model_dir / f"{MODEL_NAME}.pt"
    metadata_path = args.model_dir / f"{MODEL_NAME}.metadata.json"
    metrics_path = args.model_dir / f"{MODEL_NAME}.metrics.json"
    torch.save(model.state_dict(), params_path)

    model_metadata = {
        "recipe_dataset_hash": recipe_dataset_hash(recipes),
        "model_version": MODEL_NAME,
        "framework": "pytorch",
        "framework_version": torch.__version__,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "feature_metadata": metadata.to_dict(),
        "model_config": model.config.to_dict(),
        "training": training_summary,
    }
    metadata_path.write_text(
        json.dumps(model_metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    metrics_payload = {
        "model_version": MODEL_NAME,
        "training_data": payload.get("statistics"),
        "validation": _rounded(validation_metrics),
        "validation_by_scenario": _rounded_groups(
            evaluate_model_by_scenario(
                model,
                samples=validation_samples,
                recipes=recipes,
                metadata=metadata,
            )
        ),
        "test": _rounded(test_metrics),
        "test_by_scenario": _rounded_groups(
            evaluate_model_by_scenario(
                model,
                samples=test_samples,
                recipes=recipes,
                metadata=metadata,
            )
        ),
        "ingredient_overlap_baseline_test": _rounded(baseline_metrics),
    }
    metrics_path.write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"모델 저장: {params_path}")
    print(json.dumps(metrics_payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
