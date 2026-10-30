"""Evaluate a real OCR snapshot against image-transcribed labels, without tuning them."""
import argparse
import json
import hashlib
from pathlib import Path
from app.product_pipeline import analyze_ocr_items
from app.inventory_mapper import build_inventory_candidates
from scripts.evaluate_receipts import greedy_text_matches

ROOT = Path(__file__).resolve().parents[1]


def evaluate(rows, raw):
    products = analyze_ocr_items(raw)["products"]
    candidates = {c["row_index"]: c for c in build_inventory_candidates({"products": products})}
    matches = greedy_text_matches([r.get("alignment_text", r["text"]) for r in rows],
        [[p["product_name"], p["raw_text"]] for p in products], threshold=.55)
    details, assigned = [], set()
    tp = fp = correct = count_ok = amount_ok = 0
    for i, row in enumerate(rows):
        match = matches.get(i)
        p = products[match[0]] if match else None
        c = candidates.get(p["row_index"]) if p else None
        name = c["ingredient_master_candidate"]["canonical_name"] if c else None
        include = bool(row["names"])
        valid_name = include and name in row["names"]
        if c:
            assigned.add(c["row_index"])
            tp += int(include)
            fp += int(not include)
        correct += int(valid_name)
        valid_count = valid_name and p.get("purchase_quantity") == row.get("count")
        batch = c["inventory_batch_candidate"] if c else {}
        valid_amount = valid_name and "quantity" in row and batch.get("unit") == row["unit"] and abs(batch.get("remaining_quantity", 0) - row["quantity"]) < 1e-6
        count_ok += int(valid_count)
        amount_ok += int(valid_amount)
        details.append({"expected": row, "predicted_name": name,
            "predicted_text": p["raw_text"] if p else None,
            "name_correct": bool(valid_name) if include else name is None,
            "purchase_count_correct": bool(valid_count) if include else None,
            "amount_correct": bool(valid_amount) if "quantity" in row else None,
            "predicted_quantity": batch.get("remaining_quantity"), "predicted_unit": batch.get("unit")})
    unexpected = [c["raw_text"] for k,c in candidates.items() if k not in assigned]
    fp += len(unexpected)
    food = sum(bool(r["names"]) for r in rows)
    amounts = sum("quantity" in r and bool(r["names"]) for r in rows)
    ratio = lambda n,d: round(n/d, 4) if d else None
    return {"warning": "One development receipt; approximate text row alignment, second annotation review needed. Not general OCR accuracy.",
        "counts": {"food_rows": food, "predictions": len(candidates), "selected_true_positive":tp, "false_positive":fp,
            "missed_food":food-tp, "correct_names":correct, "correct_purchase_counts":count_ok,
            "labeled_amounts":amounts, "correct_amounts":amount_ok},
        "metrics": {"selection_precision":ratio(tp,tp+fp), "selection_recall":ratio(tp,food),
            "end_to_end_name_recall":ratio(correct,food), "end_to_end_purchase_count_recall":ratio(count_ok,food),
            "end_to_end_amount_recall":ratio(amount_ok,amounts)}, "rows":details,"unexpected":unexpected}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, default=ROOT / "output/ocr_audit.json")
    parser.add_argument("--labels", type=Path, default=ROOT / "evaluation/market_receipt_v1.json")
    args = parser.parse_args()
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    audit = json.loads(args.audit.read_text(encoding="utf-8"))["real_image"]
    if audit["filename"] != labels["source_filename"]:
        raise ValueError("정답과 영수증 파일명이 다릅니다.")
    report = evaluate(labels["rows"], audit["raw"])
    report["provenance"] = {"filename":audit["filename"],"split":labels["split"],
        "labels_sha256":hashlib.sha256(args.labels.read_bytes()).hexdigest(),
        "ocr_snapshot_sha256":hashlib.sha256(json.dumps(audit["raw"],sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
    output = ROOT / "output/market_receipt_evaluation.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in {"rows","unexpected"}}, indent=2))
