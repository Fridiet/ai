import pytest
from app.product_pipeline import analyze_ocr_items, receipt_purchase_quantity, is_adjustment_row
from app.inventory_mapper import build_inventory_candidates
from app.receipt_mapper import build_receipt_items


@pytest.mark.parametrize("line,name", [
    ("CJ_삼호어묵얇은사각 4,680 2 9,360", "어묵"),
    ("체리부로닭볶음용800g 7,180 1 7,180", "닭고기"),
    ("풀무원 발사믹올리브 6,880 1 6,880", "드레싱"),
])
def test_real_receipt_product_regressions(line, name):
    result = analyze_ocr_items([dict(text=line, confidence=.99, box=[[0,0],[700,0],[700,25],[0,25]])])
    item = build_receipt_items(build_inventory_candidates(result))[0]
    assert item["name"] == name
    assert item["raw_text"] == line


def test_verified_purchase_count_does_not_imply_known_package_size():
    line = "제리부로닭볶음용8009 7,180 1 7,180"
    result = analyze_ocr_items([dict(text=line, confidence=.99, box=[[0,0],[700,0],[700,25],[0,25]])])
    item = build_receipt_items(build_inventory_candidates(result))[0]
    assert item["quantity"] == 1
    assert item["unit"] == "개"
    assert any("포장당 용량" in note for note in item["review_notes"])


@pytest.mark.parametrize("line,quantity,unit", [
    ("우유 900ml 2,000 2 4,000", 1800, "ml"),
    ("달걀 10입 3,000 2 6,000", 20, "개"),
    ("두부 300g 2,000 3 6,000", 900, "g"),
    ("당근 1,000 2 2,000", 2, "개"),
    ("고등어(대,해동) 4,190 3 12,570", 3, "개"),
])
def test_quantity(line, quantity, unit):
    result = analyze_ocr_items([dict(text=line,confidence=.99,box=[[0,0],[700,0],[700,25],[0,25]])])
    batch = build_inventory_candidates(result)[0]['inventory_batch_candidate']
    assert (batch['remaining_quantity'], batch['unit']) == (quantity, unit)


def test_ambiguous_columns_are_not_multiplied():
    assert receipt_purchase_quantity('우유 900ml 2,000 2 3,500') is None
    assert receipt_purchase_quantity('우유 900ml 2,000') is None
    assert receipt_purchase_quantity('우유 900ml 2,000 0 0') is None


def test_discount_not_product():
    for line in ['계육 2월1추 포항점 2,160', '계육 2월1주 포항점 -2,160', '포인트 할인 500']:
        assert is_adjustment_row(line)
    assert not is_adjustment_row('할인우유 2,000 2 4,000')
