import pytest
from app.ingredient_matcher import get_ingredient_catalog
from app.ingredient_taxonomy import family
from app.team_recipe import _normalize_ingredient_name
from app.product_pipeline import analyze_ocr_items
from app.inventory_mapper import build_inventory_candidates
from app.receipt_mapper import build_receipt_items
from app.quantity_balance import ingredient_balances


@pytest.mark.parametrize('name',['백오이','청오이','가시오이','다다기오이'])
def test_printed_variety_is_kept_in_ocr_and_recipe_normalization(monkeypatch,name):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    assert get_ingredient_catalog().match(name)['standard_name']==name
    assert _normalize_ingredient_name(name)==name
    assert family(name)=='오이'
    rows=[dict(text=f'국산 {name} 2,000 1 2,000',confidence=.99,box=[[0,0],[600,0],[600,30],[0,30]])]
    item=build_receipt_items(build_inventory_candidates(analyze_ocr_items(rows)))[0]
    assert item['name']==name
    assert item['category']=='vegetable'
    assert item['location_category']=='refrigerated'


def test_general_cucumber_remains_unspecified():
    assert get_ingredient_catalog().match('오이')['standard_name']=='오이'
    assert _normalize_ingredient_name('오이')=='오이'


def test_family_is_not_automatic_inventory_substitution():
    result=ingredient_balances([{'canonical_name':'백오이','quantity':2,'unit':'개'}],
                              {'청오이':[{'remaining_quantity':5,'unit':'개'}]})
    assert result[0]['knownAvailableQuantity']==0
    assert result[0]['shortageQuantity']==2
