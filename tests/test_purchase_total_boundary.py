import pytest
from app.product_pipeline import is_purchase_total_row, analyze_ocr_items
from app.receipt_mapper import build_review_candidates


@pytest.mark.parametrize('text', ['총구 매액 2 2,200', '총 구 매 액 12 20,000원'])
def test_explicit_purchase_total(text):
    assert is_purchase_total_row(text)


@pytest.mark.parametrize('text', ['총구매액 할인 과자 2 2,200', '총구매액',
                                  '총구매액 2,200', '총구매액 0 2,200', '총구매액 2 2,200 1,000'])
def test_incomplete_or_product_text_not_a_boundary(text):
    assert not is_purchase_total_row(text)


def test_headerless_receipt_stops_after_verified_total(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    rows = [['양파','1','증정'],['당근','1','2,200'],['총구 매액','2','2,200'],
            ['객층:00담당:손*호 N0:6903 02:07']]
    items = [dict(text=t,confidence=.99,box=[[j*200,i*40],[j*200+150,i*40],
             [j*200+150,i*40+25],[j*200,i*40+25]]) for i,row in enumerate(rows) for j,t in enumerate(row)]
    result = analyze_ocr_items(items)
    assert len(result['products']) == 2
    assert [p['purchase_quantity'] for p in result['products']] == [1,1]
    assert build_review_candidates(result) == []
