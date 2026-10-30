import pytest
from app.ingredient_matcher import get_ingredient_catalog
from app.ocr_classification import apply_classification
from app.receipt_name_evidence import egg_product_head


@pytest.mark.parametrize('text', ['안심신선왕란30구', '안심신선왕란1판/30구',
    '무항생제유정란(15입)', '친환경 달걀10개', '안심계란30구',
    '안심신선특란30구', '안심신선란30구'])
def test_egg_head_precedes_tenderloin_partial_match(monkeypatch, text):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED', 'true')
    monkeypatch.setattr('app.ocr_classification.predict_product', lambda _: pytest.fail('explicit egg'))
    original = dict(standard_food_name='안심', matched_name='안심',
                    match_type='contains', source='ingredient_dictionary')
    result = apply_classification(text, original, get_ingredient_catalog())
    assert result['standard_food_name'] == '달걀'
    assert result['food_type'] == '알류'


@pytest.mark.parametrize('text', ['돼지안심500g', '안심스테이크', '왕란샐러드',
    '계란과자', '구운왕란10구', '훈제계란', '소금달걀', '소고기계란', '계란찜',
    '계란향샴푸', '반숙달걀'])
def test_meat_and_prepared_egg_products_not_plain_eggs(text):
    assert egg_product_head(text) is None


def test_meat_match_kept(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED', 'true')
    original = dict(standard_food_name='안심', matched_name='안심',
                    match_type='contains', source='ingredient_dictionary')
    assert apply_classification('돼지안심500g', original, get_ingredient_catalog()) is original
