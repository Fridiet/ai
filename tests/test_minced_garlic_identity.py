import pytest
from app.ingredient_matcher import get_ingredient_catalog
from app.ingredient_taxonomy import family
from app.team_recipe import _normalize_ingredient_name
from app.product_pipeline import analyze_ocr_items
from app.inventory_mapper import build_inventory_candidates,infer_storage_type


@pytest.mark.parametrize('name',['다진마늘','다진 마늘','마늘다진것','마늘다짐'])
def test_minced_identity_matches_recipe_and_receipt(name):
    assert get_ingredient_catalog().match(name)['standard_name']=='다진마늘'
    assert _normalize_ingredient_name(name)=='다진마늘'
    assert family('다진마늘')=='마늘'


def test_whole_garlic_not_changed():
    assert get_ingredient_catalog().match('통마늘')['standard_name']=='마늘'
    assert infer_storage_type('다진마늘','다진마늘')['storage_type']=='UNKNOWN'
    assert infer_storage_type('냉동 다진마늘','다진마늘')['storage_type']=='FROZEN'


@pytest.mark.parametrize('text',['다진마늘소스','다진마늘빵','다진마늘샴푸'])
def test_other_product_not_minced_garlic(monkeypatch,text):
    monkeypatch.setattr('app.ocr_classification.predict_product',lambda _:None)
    result=analyze_ocr_items([dict(text=text+' 1000 1 1000',confidence=.99,
        box=[[0,0],[500,0],[500,30],[0,30]])])['products'][0]
    assert result['match']['standard_food_name']!='다진마늘'


def test_volume_preserved_for_minced_product():
    analysis=analyze_ocr_items([dict(text='국내산 다진마늘 1kg 4980 1 4980',confidence=.99,
        box=[[0,0],[500,0],[500,30],[0,30]])])
    candidate=build_inventory_candidates(analysis)[0]
    assert candidate['ingredient_master_candidate']['canonical_name']=='다진마늘'
    assert candidate['inventory_batch_candidate']['remaining_quantity']==1
    assert candidate['inventory_batch_candidate']['unit']=='kg'
