import pytest
from app.product_pipeline import analyze_ocr_items
from app.receipt_mapper import build_review_candidates


def lines(*texts):
    return [dict(text=t,confidence=.99,box=[[0,i*30],[900,i*30],[900,i*30+20],[0,i*30+20]])
            for i,t in enumerate(texts)]


@pytest.mark.parametrize('name',['굴','귤','무','배','김','쌀'])
def test_single_character_food_not_noise(monkeypatch,name):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    monkeypatch.setattr('app.ocr_classification.predict_product',lambda _:pytest.fail('exact identity'))
    result=analyze_ocr_items(lines('상품명 단가 수량 금액',f'001 {name}','200413 1,980 1 1,980 #'))
    assert result['products'][0]['match']['standard_food_name']==name
    assert result['products'][0]['purchase_quantity']==1


def test_unknown_glyph_preserved_without_guess(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    monkeypatch.setattr('app.ocr_classification.predict_product',lambda _:pytest.fail('single glyph guessing'))
    result=analyze_ocr_items(lines('상품명 단가 수량 금액','001 글','200413 1,980 1 1,980 #'))
    review=build_review_candidates(result)
    assert len(review)==1
    assert review[0]['purchaseCount']==1


def test_single_character_footer_not_accepted():
    assert not analyze_ocr_items(lines('계 1980'))['products']
