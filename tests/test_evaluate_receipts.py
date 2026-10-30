from pathlib import Path

import pytest

from scripts.evaluate_receipts import (
    DEFAULT_DATASET_PATH,
    EvaluationDatasetError,
    evaluate_dataset,
    load_dataset,
    threshold_failures,
    validate_dataset,
)


def _ocr_item(text: str) -> dict:
    return {
        "text": text,
        "confidence": 0.99,
        "box": [[0, 0], [200, 0], [200, 20], [0, 20]],
    }


def test_sample_dataset_reaches_full_post_processing_score() -> None:
    payload = load_dataset(DEFAULT_DATASET_PATH)

    report = evaluate_dataset(
        payload,
        dataset_directory=DEFAULT_DATASET_PATH.parent,
    )

    assert report["summary"]["case_count"] == 2
    assert report["summary"]["metrics"] == {
        "ocr_text_recall": 1.0,
        "product_row_recall": 1.0,
        "food_selection_precision": 1.0,
        "food_selection_recall": 1.0,
        "food_selection_f1": 1.0,
        "standard_name_accuracy": 1.0,
        "end_to_end_standard_name_recall": 1.0,
    }


def test_evaluation_separates_selection_from_standard_name_accuracy() -> None:
    payload = {
        "version": 1,
        "cases": [
            {
                "id": "wrong-standard-name",
                "ocr_items": [_ocr_item("서울우유1L")],
                "expected_items": [
                    {
                        "source_text": "서울우유1L",
                        "include": True,
                        "canonical_name": "당근",
                    }
                ],
            }
        ],
    }

    report = evaluate_dataset(payload, dataset_directory=Path("."))
    metrics = report["summary"]["metrics"]

    assert metrics["food_selection_f1"] == 1.0
    assert metrics["standard_name_accuracy"] == 0.0
    assert report["cases"][0]["items"][0]["status"] == "canonical_name_mismatch"


def test_relative_image_path_uses_injected_ocr_runner(tmp_path: Path) -> None:
    image_path = tmp_path / "receipt.png"
    image_path.write_bytes(b"test-image-placeholder")
    visited_paths: list[Path] = []
    payload = {
        "version": 1,
        "cases": [
            {
                "id": "image-case",
                "image_path": "receipt.png",
                "expected_items": [
                    {
                        "source_text": "세척당근500g",
                        "include": True,
                        "canonical_name": "당근",
                    }
                ],
            }
        ],
    }

    def fake_ocr(path: Path) -> list[dict]:
        visited_paths.append(path)
        return [_ocr_item("세척당근500g")]

    report = evaluate_dataset(
        payload,
        dataset_directory=tmp_path,
        image_ocr=fake_ocr,
    )

    assert visited_paths == [image_path.resolve()]
    assert report["cases"][0]["input_mode"] == "image"
    assert report["summary"]["metrics"]["standard_name_accuracy"] == 1.0


def test_dataset_validation_rejects_ambiguous_input() -> None:
    with pytest.raises(EvaluationDatasetError, match="중 하나만"):
        validate_dataset(
            {
                "version": 1,
                "cases": [
                    {
                        "id": "ambiguous",
                        "image_path": "receipt.png",
                        "ocr_items": [],
                        "expected_items": [
                            {
                                "source_text": "당근",
                                "include": True,
                                "canonical_name": "당근",
                            }
                        ],
                    }
                ],
            }
        )


def test_threshold_failures_reports_only_failed_metrics() -> None:
    report = {
        "summary": {
            "metrics": {
                "ocr_text_recall": 0.95,
                "food_selection_f1": 0.8,
            }
        }
    }

    assert threshold_failures(
        report,
        {
            "ocr_text_recall": 0.9,
            "food_selection_f1": 0.85,
            "standard_name_accuracy": None,
        },
    ) == ["food_selection_f1: 0.8 < 0.85"]
