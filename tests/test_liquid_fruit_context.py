from app.ingredient_matcher import get_ingredient_catalog
from app.ocr_classification import apply_classification


def original():
    return dict(standard_food_name='오렌지', matched_name='오렌지',
                source='ingredient_dictionary', match_type='contains')


def test_litres_do_not_automatically_become_raw_fruit(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    monkeypatch.setattr('app.ocr_classification.predict_product', lambda _: None)
    result = apply_classification('일화)탑씨/오렌지1.5L', original(), get_ingredient_catalog())
    assert result['standard_food_name'] is None
    assert result['classification'] == 'unknown'


def test_fruit_weight_keeps_existing_match(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    monkeypatch.setattr('app.ocr_classification.predict_product', lambda _: None)
    assert apply_classification('오렌지1.5kg', original(), get_ingredient_catalog()) == original()


def test_normalized_dictionary_exact_cannot_erase_liquid_evidence(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    monkeypatch.setattr('app.ocr_classification.predict_product', lambda _: None)
    match = dict(original(), match_type='exact')
    assert apply_classification('일화)탑씨/오렌지1.5L', match, get_ingredient_catalog())['standard_food_name'] is None
