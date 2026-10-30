"""Diagnose OCR without changing recognition rules. Output stays local/ignored."""
import argparse
import json
from pathlib import Path
import requests
from app.product_pipeline import analyze_ocr_items
from app.inventory_mapper import build_inventory_candidates
from app.receipt_mapper import build_receipt_items, infer_receipt_category


def audit(image=None, verify_api=False):
    examples = []
    for line in ["우유 900ml 2,000 2 4,000", "달걀 10입 3,000 2 6,000", "두부 300g 2,000 3 6,000", "당근 1,000 2 2,000"]:
        analysis = analyze_ocr_items([{"text": line, "confidence": 0.99, "box": [[0,0],[700,0],[700,30],[0,30]]}])
        candidates = build_inventory_candidates(analysis)
        examples.append({"input": line, "items": build_receipt_items(candidates)})
    categories = {name: infer_receipt_category({"classification": "ingredient", "ingredient_master_candidate": {
        "canonical_name": name, "ingredient_type": "INGREDIENT"}}) for name in ["자두", "청양고추", "팽이버섯", "주꾸미", "양상추"]}
    result = {"synthetic_quantity_probes": examples, "category_probes": categories}
    if image:
        with Path(image).open("rb") as stream:
            response = requests.post("http://localhost:8000/ocr/raw", files={"file": ("receipt.jpg", stream, "image/jpeg")}, timeout=180)
        response.raise_for_status()
        raw = response.json()
        analysis = analyze_ocr_items(raw["items"])
        candidates = build_inventory_candidates(analysis)
        result["real_image"] = {"filename": Path(image).name, "ocr_count": raw["count"],
                                "raw": raw["items"], "analysis": analysis,
                                "candidates": candidates, "receipt_items": build_receipt_items(candidates)}
        if verify_api:
            with Path(image).open("rb") as stream:
                response = requests.post("http://localhost:8000/ocr", files={"file": ("receipt.jpg", stream, "image/jpeg")}, timeout=180)
            response.raise_for_status()
            items = response.json()["items"]
            assert items
            for item in items:
                assert item["rawText"]
                assert item["reviewNotes"]
                assert item["quantity"] > 0
            result["live_receipt_api"] = {"verified": True, "items": items}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image")
    parser.add_argument("--verify-api", action="store_true")
    args = parser.parse_args()
    result = audit(args.image, args.verify_api)
    output = Path("output/ocr_audit.json")
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "real_ocr_count": result.get("real_image", {}).get("ocr_count"),
                      "real_names": [r["name"] for r in result.get("real_image", {}).get("receipt_items", [])],
                      "quantity_probes": result["synthetic_quantity_probes"], "categories": result["category_probes"]}, ensure_ascii=True, default=str))
