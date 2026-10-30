from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.ingredient_matcher import lookup_key
from app.inventory_mapper import build_inventory_candidates
from app.product_pipeline import analyze_ocr_items, clean_ocr_items, group_product_rows


AI_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_PATH = AI_ROOT / "evaluation" / "receipt_ground_truth.sample.json"
DEFAULT_OUTPUT_PATH = AI_ROOT / "output" / "receipt_evaluation.json"


class EvaluationDatasetError(ValueError):
    """Raised when a receipt evaluation dataset is invalid."""


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 4)


def _validate_ocr_item(item: Any, *, case_id: str, item_index: int) -> None:
    if not isinstance(item, dict):
        raise EvaluationDatasetError(
            f"{case_id}.ocr_items[{item_index}]는 JSON 객체여야 합니다."
        )
    if not str(item.get("text") or "").strip():
        raise EvaluationDatasetError(
            f"{case_id}.ocr_items[{item_index}].text가 비어 있습니다."
        )
    confidence = item.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise EvaluationDatasetError(
            f"{case_id}.ocr_items[{item_index}].confidence는 0~1이어야 합니다."
        )
    box = item.get("box")
    if not isinstance(box, list) or len(box) < 4:
        raise EvaluationDatasetError(
            f"{case_id}.ocr_items[{item_index}].box는 네 점 이상이어야 합니다."
        )


def validate_dataset(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise EvaluationDatasetError("데이터셋 최상위 값은 JSON 객체여야 합니다.")
    if payload.get("version") != 1:
        raise EvaluationDatasetError("지원하는 데이터셋 version은 1입니다.")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluationDatasetError("cases에는 평가 케이스가 한 개 이상 필요합니다.")

    seen_ids: set[str] = set()
    for case_index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise EvaluationDatasetError(f"cases[{case_index}]는 JSON 객체여야 합니다.")
        case_id = str(case.get("id") or "").strip()
        if not case_id:
            raise EvaluationDatasetError(f"cases[{case_index}].id가 비어 있습니다.")
        if case_id in seen_ids:
            raise EvaluationDatasetError(f"중복된 케이스 id입니다: {case_id}")
        seen_ids.add(case_id)

        has_image = bool(str(case.get("image_path") or "").strip())
        has_snapshot = "ocr_items" in case
        if has_image == has_snapshot:
            raise EvaluationDatasetError(
                f"{case_id}에는 image_path와 ocr_items 중 하나만 있어야 합니다."
            )
        if has_snapshot:
            ocr_items = case.get("ocr_items")
            if not isinstance(ocr_items, list):
                raise EvaluationDatasetError(f"{case_id}.ocr_items는 배열이어야 합니다.")
            for item_index, item in enumerate(ocr_items):
                _validate_ocr_item(item, case_id=case_id, item_index=item_index)

        expected_items = case.get("expected_items")
        if not isinstance(expected_items, list) or not expected_items:
            raise EvaluationDatasetError(
                f"{case_id}.expected_items에는 한 개 이상의 정답 행이 필요합니다."
            )
        for item_index, item in enumerate(expected_items):
            if not isinstance(item, dict):
                raise EvaluationDatasetError(
                    f"{case_id}.expected_items[{item_index}]는 JSON 객체여야 합니다."
                )
            if not str(item.get("source_text") or "").strip():
                raise EvaluationDatasetError(
                    f"{case_id}.expected_items[{item_index}].source_text가 비어 있습니다."
                )
            if not isinstance(item.get("include"), bool):
                raise EvaluationDatasetError(
                    f"{case_id}.expected_items[{item_index}].include는 boolean이어야 합니다."
                )
            canonical_name = item.get("canonical_name")
            if item["include"] and not str(canonical_name or "").strip():
                raise EvaluationDatasetError(
                    f"{case_id}.expected_items[{item_index}]의 canonical_name이 필요합니다."
                )
            if not item["include"] and canonical_name is not None:
                raise EvaluationDatasetError(
                    f"{case_id}.expected_items[{item_index}]의 canonical_name은 null이어야 합니다."
                )
    return payload


def load_dataset(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise EvaluationDatasetError(f"평가 데이터셋이 없습니다: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvaluationDatasetError(f"평가 데이터셋 JSON이 올바르지 않습니다: {exc}") from exc
    return validate_dataset(payload)


def _read_image_ocr(image_path: Path) -> list[dict[str, Any]]:
    import cv2
    import numpy as np

    from app.ocr import run_ocr

    image = cv2.imdecode(
        np.frombuffer(image_path.read_bytes(), dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    if image is None:
        raise EvaluationDatasetError(f"이미지를 해석할 수 없습니다: {image_path}")
    return run_ocr(image)


def _case_ocr_items(
    case: dict[str, Any],
    *,
    dataset_directory: Path,
    image_ocr: Callable[[Path], list[dict[str, Any]]] | None,
) -> tuple[list[dict[str, Any]], str, str | None]:
    if "ocr_items" in case:
        return [dict(item) for item in case["ocr_items"]], "snapshot", None

    image_path = Path(case["image_path"])
    if not image_path.is_absolute():
        image_path = dataset_directory / image_path
    image_path = image_path.resolve()
    if not image_path.is_file():
        raise EvaluationDatasetError(
            f"{case['id']}의 영수증 이미지가 없습니다: {image_path}"
        )
    runner = image_ocr or _read_image_ocr
    return runner(image_path), "image", str(image_path)


def text_similarity(left: str, right: str) -> float:
    left_key = lookup_key(left)
    right_key = lookup_key(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    shorter, longer = sorted((left_key, right_key), key=len)
    contained_score = 0.0
    if len(shorter) >= 3 and shorter in longer:
        contained_score = 0.9 + 0.1 * len(shorter) / len(longer)
    return min(1.0, max(contained_score, SequenceMatcher(None, left_key, right_key).ratio()))


def _best_similarity(expected: str, alternatives: list[str]) -> float:
    return max((text_similarity(expected, value) for value in alternatives), default=0.0)


def greedy_text_matches(
    expected_texts: list[str],
    predicted_alternatives: list[list[str]],
    *,
    threshold: float,
    compatible=None,
) -> dict[int, tuple[int, float]]:
    candidates: list[tuple[float, int, int]] = []
    for expected_index, expected_text in enumerate(expected_texts):
        for predicted_index, alternatives in enumerate(predicted_alternatives):
            if compatible is not None and not compatible(expected_index, predicted_index):
                continue
            score = _best_similarity(expected_text, alternatives)
            if score >= threshold:
                candidates.append((score, expected_index, predicted_index))

    matches: dict[int, tuple[int, float]] = {}
    used_predictions: set[int] = set()
    for score, expected_index, predicted_index in sorted(candidates, reverse=True):
        if expected_index in matches or predicted_index in used_predictions:
            continue
        matches[expected_index] = (predicted_index, round(score, 4))
        used_predictions.add(predicted_index)
    return matches


def _metrics_from_counts(counts: dict[str, int]) -> dict[str, float | None]:
    selection_precision = _ratio(
        counts["selection_true_positive"],
        counts["selection_true_positive"] + counts["selection_false_positive"],
    )
    selection_recall = _ratio(
        counts["selection_true_positive"],
        counts["selection_true_positive"] + counts["selection_false_negative"],
    )
    return {
        "ocr_text_recall": _ratio(counts["ocr_text_matched"], counts["expected_rows"]),
        "product_row_recall": _ratio(
            counts["product_rows_matched"], counts["expected_rows"]
        ),
        "food_selection_precision": selection_precision,
        "food_selection_recall": selection_recall,
        "food_selection_f1": _f1(selection_precision, selection_recall),
        "standard_name_accuracy": _ratio(
            counts["canonical_correct"], counts["canonical_evaluated"]
        ),
        "end_to_end_standard_name_recall": _ratio(
            counts["canonical_correct"], counts["expected_food_rows"]
        ),
    }


def evaluate_case(
    case: dict[str, Any],
    *,
    dataset_directory: Path,
    min_confidence: float = 0.35,
    match_threshold: float = 0.72,
    row_match_threshold: float = 0.55,
    image_ocr: Callable[[Path], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    ocr_items, input_mode, image_path = _case_ocr_items(
        case,
        dataset_directory=dataset_directory,
        image_ocr=image_ocr,
    )
    cleaned = clean_ocr_items(ocr_items, min_confidence=min_confidence)
    raw_rows = group_product_rows(cleaned)
    raw_row_texts = [" ".join(item["text"] for item in row) for row in raw_rows]
    analysis = analyze_ocr_items(
        ocr_items,
        min_confidence=min_confidence,
        match_threshold=match_threshold,
    )
    products = analysis["products"]
    candidates = build_inventory_candidates(analysis)
    candidate_by_row = {candidate["row_index"]: candidate for candidate in candidates}

    expected_items = case["expected_items"]
    expected_texts = [item["source_text"] for item in expected_items]
    ocr_matches = greedy_text_matches(
        expected_texts,
        [[text] for text in raw_row_texts],
        threshold=row_match_threshold,
    )
    product_matches = greedy_text_matches(
        expected_texts,
        [
            [product["raw_text"], product["product_name"], product["normalized_name"]]
            for product in products
        ],
        threshold=row_match_threshold,
    )

    counts = {
        "expected_rows": len(expected_items),
        "expected_food_rows": sum(1 for item in expected_items if item["include"]),
        "ocr_text_matched": len(ocr_matches),
        "product_rows_matched": len(product_matches),
        "selection_true_positive": 0,
        "selection_false_positive": 0,
        "selection_false_negative": 0,
        "canonical_evaluated": 0,
        "canonical_correct": 0,
    }
    details: list[dict[str, Any]] = []
    matched_selected_rows: set[int] = set()

    for expected_index, expected in enumerate(expected_items):
        product_match = product_matches.get(expected_index)
        product = products[product_match[0]] if product_match else None
        candidate = candidate_by_row.get(product["row_index"]) if product else None
        selected = candidate is not None
        predicted_name = (
            candidate["ingredient_master_candidate"]["canonical_name"]
            if candidate
            else None
        )
        if candidate:
            matched_selected_rows.add(candidate["row_index"])

        if expected["include"]:
            if selected:
                counts["selection_true_positive"] += 1
                counts["canonical_evaluated"] += 1
                if predicted_name == expected["canonical_name"]:
                    counts["canonical_correct"] += 1
            else:
                counts["selection_false_negative"] += 1
        elif selected:
            counts["selection_false_positive"] += 1

        if not product_match:
            status = "product_row_missed"
        elif expected["include"] and not selected:
            status = "food_not_selected"
        elif not expected["include"] and selected:
            status = "non_food_selected"
        elif expected["include"] and predicted_name != expected["canonical_name"]:
            status = "canonical_name_mismatch"
        else:
            status = "correct"

        details.append(
            {
                "source_text": expected["source_text"],
                "expected_include": expected["include"],
                "expected_canonical_name": expected["canonical_name"],
                "ocr_text_detected": expected_index in ocr_matches,
                "product_row_detected": product_match is not None,
                "row_match_score": product_match[1] if product_match else None,
                "predicted_product_name": product["product_name"] if product else None,
                "predicted_include": selected,
                "predicted_canonical_name": predicted_name,
                "status": status,
            }
        )

    unexpected_predictions: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate["row_index"] in matched_selected_rows:
            continue
        counts["selection_false_positive"] += 1
        unexpected_predictions.append(
            {
                "source_text": candidate["recognized_product_name"],
                "canonical_name": candidate["ingredient_master_candidate"]["canonical_name"],
                "row_index": candidate["row_index"],
            }
        )

    return {
        "id": case["id"],
        "description": case.get("description"),
        "input_mode": input_mode,
        "image_path": image_path,
        "ocr_item_count": len(ocr_items),
        "product_row_count": len(products),
        "selected_item_count": len(candidates),
        "counts": counts,
        "metrics": _metrics_from_counts(counts),
        "items": details,
        "unexpected_predictions": unexpected_predictions,
    }


def evaluate_dataset(
    payload: dict[str, Any],
    *,
    dataset_directory: Path,
    min_confidence: float = 0.35,
    match_threshold: float = 0.72,
    row_match_threshold: float = 0.55,
    image_ocr: Callable[[Path], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    validate_dataset(payload)
    case_results = [
        evaluate_case(
            case,
            dataset_directory=dataset_directory,
            min_confidence=min_confidence,
            match_threshold=match_threshold,
            row_match_threshold=row_match_threshold,
            image_ocr=image_ocr,
        )
        for case in payload["cases"]
    ]
    aggregate_counts = {
        key: sum(result["counts"][key] for result in case_results)
        for key in case_results[0]["counts"]
    }
    return {
        "dataset_version": payload["version"],
        "description": payload.get("description"),
        "configuration": {
            "min_confidence": min_confidence,
            "match_threshold": match_threshold,
            "row_match_threshold": row_match_threshold,
        },
        "summary": {
            "case_count": len(case_results),
            "counts": aggregate_counts,
            "metrics": _metrics_from_counts(aggregate_counts),
        },
        "cases": case_results,
    }


def threshold_failures(
    report: dict[str, Any], thresholds: dict[str, float | None]
) -> list[str]:
    metrics = report["summary"]["metrics"]
    failures: list[str] = []
    for metric_name, minimum in thresholds.items():
        if minimum is None:
            continue
        actual = metrics.get(metric_name)
        if actual is None or actual < minimum:
            failures.append(f"{metric_name}: {actual} < {minimum}")
    return failures


def _metric_text(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="영수증 OCR·상품 행·식재료 선별·표준명 정확도를 평가합니다."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--min-confidence", type=float, default=0.35)
    parser.add_argument("--match-threshold", type=float, default=0.72)
    parser.add_argument("--row-match-threshold", type=float, default=0.55)
    parser.add_argument("--min-ocr-text-recall", type=float)
    parser.add_argument("--min-product-row-recall", type=float)
    parser.add_argument("--min-food-selection-f1", type=float)
    parser.add_argument("--min-standard-name-accuracy", type=float)
    args = parser.parse_args()

    for argument_name in (
        "min_confidence",
        "match_threshold",
        "row_match_threshold",
        "min_ocr_text_recall",
        "min_product_row_recall",
        "min_food_selection_f1",
        "min_standard_name_accuracy",
    ):
        value = getattr(args, argument_name)
        if value is not None and not 0 <= value <= 1:
            parser.error(f"--{argument_name.replace('_', '-')}는 0~1이어야 합니다.")

    dataset_path = args.dataset.resolve()
    try:
        payload = load_dataset(dataset_path)
        report = evaluate_dataset(
            payload,
            dataset_directory=dataset_path.parent,
            min_confidence=args.min_confidence,
            match_threshold=args.match_threshold,
            row_match_threshold=args.row_match_threshold,
        )
    except EvaluationDatasetError as exc:
        parser.error(str(exc))

    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    metrics = report["summary"]["metrics"]
    print(f"평가 완료: {report['summary']['case_count']}개 영수증 케이스")
    print(f"OCR 상품명 재현율: {_metric_text(metrics['ocr_text_recall'])}")
    print(f"상품 행 재현율: {_metric_text(metrics['product_row_recall'])}")
    print(f"식재료 선별 F1: {_metric_text(metrics['food_selection_f1'])}")
    print(f"표준명 정확도: {_metric_text(metrics['standard_name_accuracy'])}")
    print(f"상세 결과: {output_path}")

    failures = threshold_failures(
        report,
        {
            "ocr_text_recall": args.min_ocr_text_recall,
            "product_row_recall": args.min_product_row_recall,
            "food_selection_f1": args.min_food_selection_f1,
            "standard_name_accuracy": args.min_standard_name_accuracy,
        },
    )
    if failures:
        for failure in failures:
            print(f"기준 미달: {failure}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
