import pytest

from app.receipt_name_evidence import explicit_food_head
from app.ocr_classification import apply_classification
from app.ingredient_matcher import get_ingredient_catalog


@pytest.mark.parametrize('text', ['감자침사워크림', '감자침어니언150g', '감자침치즈'])
def test_single_damaged_chip_glyph_requires_stem_and_flavor(text):
    assert explicit_food_head(text) == ('감자칩', '과자')


@pytest.mark.parametrize('text', [
    '사워크림', '감자', '감자침', '김자침사워크림',
    '감자침사워크림소스', '감자침사워크림맛우유',
])
def test_no_unbounded_fuzzy_repair(text):
    assert explicit_food_head(text) != ('감자칩', '과자')


def test_product_identity_precedes_flavor_substring(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED', 'true')
    original = dict(standard_food_name='사워크림', matched_name='사워크림',
                    source='ingredient_dictionary', match_type='contains')
    result = apply_classification('감자침사워크림', original, get_ingredient_catalog())
    assert result['standard_food_name'] == '감자칩'
    assert result['classification'] == 'processed_food'
