from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch

from app.ml.features import FeatureMetadata
from app.ml.trainer import (
    evaluate_model,
    evaluate_model_by_scenario,
    evaluate_overlap_baseline,
)
from app.ml.two_tower import HybridTwoTower, TwoTowerConfig


AI_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECIPES_PATH = AI_ROOT / "app" / "data" / "recipes_ko.json"
DEFAULT_DATA_PATH = AI_ROOT / "output" / "two_tower" / "training_data.json"
DEFAULT_MODEL_DIR = AI_ROOT / "app" / "models"
MODEL_NAME = "two_tower_pytorch_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fridiet Two-Tower 모델 평가")
    parser.add_argument("--recipes", type=Path, default=DEFAULT_RECIPES_PATH)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument(
        "--split",
        choices=("validation", "test"),
        default="test",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    recipes = json.loads(args.recipes.read_text(encoding="utf-8"))
    payload = json.loads(args.data.read_text(encoding="utf-8"))
    bundle = json.loads(
        (args.model_dir / f"{MODEL_NAME}.metadata.json").read_text(encoding="utf-8")
    )
    metadata = FeatureMetadata.from_dict(bundle["feature_metadata"])
    model = HybridTwoTower(TwoTowerConfig.from_dict(bundle["model_config"]))
    state_dict = torch.load(
        args.model_dir / f"{MODEL_NAME}.pt",
        map_location="cpu",
        weights_only=True,
    )
    model.load_state_dict(state_dict)

    samples = [
        sample for sample in payload["samples"] if sample["split"] == args.split
    ]
    result = {
        "model_version": bundle["model_version"],
        "split": args.split,
        "two_tower": evaluate_model(
            model,
            samples=samples,
            recipes=recipes,
            metadata=metadata,
        ),
        "two_tower_by_scenario": evaluate_model_by_scenario(
            model,
            samples=samples,
            recipes=recipes,
            metadata=metadata,
        ),
        "ingredient_overlap_baseline": evaluate_overlap_baseline(
            samples=samples,
            recipes=recipes,
            metadata=metadata,
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
