from app.product_pipeline import analyze_ocr_items
from app.receipt_mapper import build_review_candidates


def test_only_unmatched_priced_non_household_rows_are_reviewed():
    lines = ["미확인농산품 2,000 2 4,000", "샴푸 2,000 2 4,000", "포인트 할인 500", "당근 2,000 2 4,000"]
    raw = [{"text":s,"confidence":.99,"box":[[0,i*50],[600,i*50],[600,i*50+20],[0,i*50+20]]} for i,s in enumerate(lines)]
    result = build_review_candidates(analyze_ocr_items(raw))
    assert len(result) == 1 and result[0]["purchaseCount"] == 2
    assert "미확인농산품" in result[0]["rawText"]
