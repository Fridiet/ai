import pytest

from app.receipt_name_evidence import explicit_food_head
from app.ingredient_matcher import get_ingredient_catalog
from app.ocr_classification import apply_classification


@pytest.mark.parametrize('text,name', [
    ('오븐에구문호두250', '호두'), ('구운아몬드500', '아몬드'),
    ('생피스타치오200g', '피스타치오'), ('캐슈너트', '캐슈넛'),
])
def test_explicit_nut_head_survives_size_and_marketing(text, name):
    assert explicit_food_head(text) == (name, '견과류')


@pytest.mark.parametrize('text', [
    '호두과자', '아몬드음료250', '초콜릿아몬드250', '허니버터아몬드250',
    '호두아몬드250', '믹스호두250', '아몬드가루250', '호두샴푸',
    '크라운아몬드250', '호두향250',
])
def test_compound_or_mixed_product_is_not_plain_nut(text):
    assert explicit_food_head(text) not in {('호두', '견과류'), ('아몬드', '견과류')}


def test_uncertain_original_can_recover_legible_nut(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED', 'true')
    original = dict(standard_food_name=None, match_type='none', source='none')
    result = apply_classification('오븐에구문호두250', original, get_ingredient_catalog())
    assert result['standard_food_name'] == '호두'
