import pytest
from app.receipt_name_evidence import explicit_food_head
from app.product_pipeline import analyze_ocr_items


@pytest.mark.parametrize('text', [
    '레이즈케찹맛감자', '레이즈 케첩맛 감자칩', '레이즈솔트&비네거',
    '레이즈 솔트 앤 비네거 155g', "LAY’S Salt & Vinegar", "LAY'S Ketchup",
])
def test_known_brand_and_flavor_identify_chips(text):
    assert explicit_food_head(text) == ('감자칩', '과자')


@pytest.mark.parametrize('text', [
    '레이즈', '레이즈케찹맛감자소스', '레이즈솔트비네거드레싱',
    '레이즈솔트비네거세트', '레이츠케찹맛감자', '타사케찹맛감자',
    '케찹', '비네거', '레이즈케찹맛감자키링',
])
def test_brand_snippets_and_different_product_heads_are_not_chips(text):
    assert explicit_food_head(text) != ('감자칩', '과자')


def test_receipt_keeps_verified_quantity_and_original_text(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED', 'false')
    raw = '02 레이즈케찹맛감자 4,990 1 4,990'
    analysis = analyze_ocr_items([{'text': raw, 'confidence': .99,
                                 'box': [[0,0],[500,0],[500,30],[0,30]]}])
    product = analysis['products'][0]
    assert product['match']['standard_food_name'] == '감자칩'
    assert product['purchase_quantity'] == 1
    assert product['raw_text'] == raw
