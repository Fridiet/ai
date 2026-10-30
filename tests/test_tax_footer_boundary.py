import pytest
from app.product_pipeline import is_tax_total_row, analyze_ocr_items
from app.receipt_mapper import build_review_candidates


@pytest.mark.parametrize('text',["과세물' 품 25,691",'면세" 물품 600','(*) 면세 물품 가액 32,690','면세물품금액: 8,480'])
def test_punctuation_does_not_hide_tax_total(text):
    assert is_tax_total_row(text)


@pytest.mark.parametrize('text',['과세물품 홍보 과자 1,000','면세 물품','면세물품 1,000 2 2,000','과세식품세트 3,000'])
def test_other_product_or_incomplete_label_is_not_boundary(text):
    assert not is_tax_total_row(text)


def test_tax_footer_noise_not_returned_as_unknown_food(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    texts=['상품명 단가 수량 금액','양파 1,000 1 1,000',"과세물' 품 25,691",'불가 세 2,569','상할 품 액 35,850','인 액 6,990']
    rows=[{'text':t,'confidence':.99,'box':[[0,i*50],[700,i*50],[700,i*50+30],[0,i*50+30]]} for i,t in enumerate(texts)]
    result=analyze_ocr_items(rows)
    assert len(result['products'])==1
    assert result['products'][0]['product_name']=='양파'
    assert build_review_candidates(result)==[]
