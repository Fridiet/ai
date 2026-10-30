import pytest
from app.ingredient_matcher import get_ingredient_catalog
from app.ocr_classification import apply_classification
from app.receipt_name_evidence import unresolved_flavor_context


@pytest.mark.parametrize('name', ['사워크림','바닐라','초콜릿','치즈','케첩','마요네즈'])
def test_unknown_prefix_is_not_proof_of_flavor_ingredient(monkeypatch, name):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    original = dict(standard_food_name=name,matched_name=name,
                    source='ingredient_dictionary',match_type='contains')
    result=apply_classification('김자침'+name,original,get_ingredient_catalog())
    assert result['standard_food_name'] is None
    assert result['classification']=='unknown'


@pytest.mark.parametrize('text', ['사워크림','사워크림 200g','02 사워크림 200g'])
def test_whole_plain_product_remains_available(monkeypatch,text):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    assert not unresolved_flavor_context(text,'사워크림')
    original=dict(standard_food_name='사워크림',matched_name='사워크림',
                  source='ingredient_dictionary',match_type='contains')
    assert apply_classification(text,original,get_ingredient_catalog())['standard_food_name']=='사워크림'


def test_model_abstention_is_not_bypassed_by_partial_dictionary(monkeypatch):
    monkeypatch.setattr('app.ocr_classification.predict_product',lambda _:dict(
        needs_review=True,is_food=True,canonical_name='감자칩',model_score=.6))
    original=dict(standard_food_name='사워크림',matched_name='사워크림',
                  source='ingredient_dictionary',match_type='exact')
    assert apply_classification('김자침 사워크림',original,get_ingredient_catalog())['standard_food_name'] is None


def test_complete_chip_head_still_wins(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    original=dict(standard_food_name='사워크림',matched_name='사워크림',
                  source='ingredient_dictionary',match_type='contains')
    assert apply_classification('감자칩사워크림',original,get_ingredient_catalog())['standard_food_name']=='감자칩'


def test_complete_caramel_chocolate_is_not_an_unknown_flavor(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    original=dict(standard_food_name='초콜릿',matched_name='초콜릿',
                  source='ingredient_dictionary',match_type='contains')
    assert apply_classification('노브랜드카라멜초콜릿',original,get_ingredient_catalog())['standard_food_name']=='초콜릿'
    assert apply_classification('김자침카라멜초콜릿',original,get_ingredient_catalog())['standard_food_name'] is None
