"""Local-only OCR review preparation. Predictions are not training labels."""
import argparse
import hashlib
import json
import mimetypes
from pathlib import Path
import requests
from app.product_pipeline import analyze_ocr_items
from app.inventory_mapper import build_inventory_candidates
from app.receipt_mapper import build_receipt_items

EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def image_manifest(folder: Path) -> list[dict]:
    if not folder.is_dir():
        raise ValueError("이미지 폴더가 존재하지 않습니다.")
    records = []
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.suffix.lower() in EXTENSIONS:
            records.append({"image_path": str(path.resolve()), "filename": path.name,
                "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "source_split": "test", "training_eligible": False,
                "store_type": None, "reviewed": False, "expected_items": None})
    return records


def write_json(path: Path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def run(folder: Path, output: Path, limit: int | None = None):
    # A timeout/error is recorded and retryable. Completed raw OCR is reused.
    manifest = image_manifest(folder)
    if limit is not None:
        manifest = manifest[:limit]
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "raw"
    cache.mkdir(exist_ok=True)
    records = []
    for index, entry in enumerate(manifest, 1):
        path = Path(entry["image_path"])
        cached = cache / (entry["image_sha256"] + ".json")
        try:
            if cached.exists():
                raw = json.loads(cached.read_text(encoding="utf-8"))
            else:
                with path.open("rb") as image:
                    response = requests.post("http://127.0.0.1:8000/ocr/raw",
                        files={"file": (path.name, image, mimetypes.guess_type(path.name)[0] or "image/jpeg")}, timeout=180)
                response.raise_for_status()
                raw = response.json()
                if not isinstance(raw.get("items"), list):
                    raise ValueError("OCR 응답의 items가 올바르지 않습니다.")
                write_json(cached, raw)
            analysis = analyze_ocr_items(raw["items"])
            entry.update({"status": "ok", "ocr_count": len(raw["items"]),
                "raw_cache": str(cached.resolve()), "product_candidates": analysis["products"],
                "predicted_items": build_receipt_items(build_inventory_candidates(analysis))})
        except Exception as exc:
            entry.update({"status": "error", "error_type": type(exc).__name__})
        records.append(entry)
        write_json(output / "review.json", {"purpose": "manual_review_only", "records": records})
        print(f"[{index}/{len(manifest)}] {path.name}: {entry['status']}", flush=True)
    summary = {"images": len(manifest), "unique_images": len({r['image_sha256'] for r in records}),
        "completed": sum(r["status"] == "ok" for r in records),
        "errors": sum(r["status"] == "error" for r in records),
        "empty_predictions": sum(r["status"] == "ok" and not r["predicted_items"] for r in records),
        "training_performed": False, "ground_truth_available": False}
    write_json(output / "summary.json", summary)
    print(json.dumps(summary), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("output/local_receipt_review"))
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("limit은 양수여야 합니다.")
    run(args.images, args.output, args.limit)
