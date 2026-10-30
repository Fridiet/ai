"""Prepare local review candidates, never trusted labels or training data.

Run with: python -m scripts.prepare_korie_review --csv PATH --split train
The caller supplies an authorized local CSV. No download or catalog writes.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
from app.product_pipeline import extract_product_name, normalize_product_name


def prepare(path: Path, split: str) -> dict:
    if split not in {"train", "validation", "test"}:
        raise ValueError("원본 데이터의 split을 지정하세요.")
    records = []
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not {"image_name", "Item_Name"}.issubset(reader.fieldnames or []):
            raise ValueError("KORIE image_name, Item_Name 열이 필요합니다.")
        for line, row in enumerate(reader, 2):
            receipt_id, raw = row["image_name"].strip(), row["Item_Name"].strip()
            if not receipt_id or not raw:
                raise ValueError(f"{line}행의 영수증 ID 또는 상품명이 비어 있습니다.")
            records.append({"receipt_id": receipt_id, "source_row": line, "split": split,
                "raw_text": raw, "candidate_name": normalize_product_name(extract_product_name(raw)),
                "source_class": row.get("class", ""), "store_type": None,
                "is_food": None, "canonical_name": None, "reviewed": False})
    return {"source": "KORIE", "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "purpose": "manual_review_only", "split": split, "rows": len(records),
            "receipts": len({r["receipt_id"] for r in records}), "records": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--split", choices=["train", "validation", "test"], required=True)
    parser.add_argument("--output", type=Path, default=Path("output/korie_review.json"))
    args = parser.parse_args()
    result = prepare(args.csv, args.split)
    if args.output.resolve() == args.csv.resolve():
        parser.error("출력 경로는 원본 CSV와 달라야 합니다.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, ensure_ascii=False, indent=2)
    print(f"검수 후보: {result['rows']}행 / {result['receipts']}개 영수증. 학습·사전 반영 없음.")


if __name__ == "__main__":
    main()
