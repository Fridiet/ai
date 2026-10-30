import pytest
from app.receipt_name_evidence import explicit_food_head
from app.inventory_mapper import parse_inventory_quantity, build_inventory_candidates
from app.product_pipeline import analyze_ocr_items
from app.receipt_mapper import build_receipt_items


def test_plain_milk_identity_does_not_prove_damaged_volume(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    text='남양)1등급아침에우유 900m1*2입'
    assert explicit_food_head(text)==('우유','유제품')
    assert parse_inventory_quantity(text)['source']=='DEFAULT'
    analysis=analyze_ocr_items([dict(text=text+' 4,680 1 4,680',confidence=.99,
                                   box=[[0,0],[700,0],[700,30],[0,30]])])
    candidates=build_inventory_candidates(analysis)
    assert candidates[0]['ingredient_master_candidate']['canonical_name']=='우유'
    assert 'PACKAGE_SIZE_REQUIRES_CONFIRMATION' in candidates[0]['warnings']
    item=build_receipt_items(candidates)[0]
    assert item['quantity']==1 and item['unit']=='개'
    assert any('포장당 용량' in note for note in item['review_notes'])


def test_clear_volume_still_parses_multipack():
    assert parse_inventory_quantity('남양우유900ml*2입')['quantity']==1800


@pytest.mark.parametrize('text',['딸기우유900m1','초코우유900ml','서울우유쿠키900ml','남양우유샴푸','아몬드우유900ml'])
def test_other_product_is_not_plain_milk(text):
    assert explicit_food_head(text)!=('우유','유제품')
