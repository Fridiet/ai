from __future__ import annotations

import argparse
import json
from pathlib import Path
from app.ml.dataset_identity import recipe_dataset_hash

from app.ml.training_data import (
    DEFAULT_CONTEXTS_PER_RECIPE,
    DEFAULT_HARD_NEGATIVE_COUNT,
    build_training_payload,
)


AI_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECIPES_PATH = AI_ROOT / "app" / "data" / "recipes_ko.json"
DEFAULT_OUTPUT_PATH = AI_ROOT / "output" / "two_tower" / "training_data.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Two-Tower 학습 context 생성")
    parser.add_argument("--recipes", type=Path, default=DEFAULT_RECIPES_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--contexts-per-recipe",
        type=int,
        default=DEFAULT_CONTEXTS_PER_RECIPE,
    )
    parser.add_argument(
        "--hard-negatives",
        type=int,
        default=DEFAULT_HARD_NEGATIVE_COUNT,
    )
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    recipes = json.loads(args.recipes.read_text(encoding="utf-8"))
    payload = build_training_payload(
        recipes,
        contexts_per_recipe=args.contexts_per_recipe,
        hard_negative_count=args.hard_negatives,
        seed=args.seed,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload['recipe_dataset_hash'] = recipe_dataset_hash(recipes)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    statistics = payload["statistics"]
    split_counts = statistics["split_counts"]
    metadata = payload["metadata"]
    print(f"저장 위치: {args.output}")
    print(
        f"레시피 {len(recipes):,}개, 재료 vocabulary "
        f"{len(metadata['ingredient_vocab']):,}개, 총 context "
        f"{len(payload['samples']):,}개"
    )
    print(
        "분할: "
        + ", ".join(
            f"{name}={count:,}" for name, count in sorted(split_counts.items())
        )
    )
    print(
        "시나리오: "
        + ", ".join(
            f"{name}={count:,}"
            for name, count in statistics["scenario_counts"].items()
        )
    )
    print(
        "건강 목표: "
        + ", ".join(
            f"{name}={count:,}" for name, count in statistics["goal_counts"].items()
        )
    )
    print(f"샘플당 hard negative: 최대 {args.hard_negatives}개")


if __name__ == "__main__":
    main()
