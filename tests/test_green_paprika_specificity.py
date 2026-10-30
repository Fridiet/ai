import pytest
from app.ingredient_matcher import get_ingredient_catalog
from app.team_recipe import _normalize_ingredient_name
from app.product_pipeline import analyze_ocr_items
from app.inventory_mapper import build_inventory_candidates
from app.receipt_mapper import build_receipt_items
from app.quantity_balance import ingredient_balances


@pytest.mark.parametrize('name',['청파프리카','초록파프리카','녹색파프리카'])
def test_specific_name_and_pack_quantity(name,monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    assert _normalize_ingredient_name(name)=='청파프리카'
    result=analyze_ocr_items([dict(text=name+'3개/1봉 1,280 2 2,560',confidence=.99,
                                  box=[[0,0],[600,0],[600,30],[0,30]])])
    item=build_receipt_items(build_inventory_candidates(result))[0]
    assert item['name']=='청파프리카'
    assert item['quantity']==6 and item['unit']=='개'
    assert item['category']=='vegetable'
    assert item['location_category']=='refrigerated'


def test_general_paprika_and_bell_pepper_are_not_green_paprika():
    for name in ['파프리카','피망']:
        assert get_ingredient_catalog().match(name)['standard_name']==name


def test_family_does_not_prove_specific_stock():
    result=ingredient_balances([dict(canonical_name='청파프리카',quantity=3,unit='개')],
                              {'파프리카':[dict(remaining_quantity=5,unit='개')]})
    assert result[0]['shortageQuantity']==3
