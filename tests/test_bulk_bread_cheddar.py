import pytest
from app.receipt_name_evidence import explicit_food_head
from app.inventory_mapper import parse_inventory_quantity,build_inventory_candidates
from app.product_pipeline import analyze_ocr_items
from app.ingredient_taxonomy import family


@pytest.mark.parametrize('text,name',[
    ('뉴디너롤36CT','디너롤'),('디너롤','디너롤'),
    ('KS마일드체다907G','체다치즈'),('마일드체다','체다치즈'),
])
def test_complete_bulk_product_identity(text,name):
    assert explicit_food_head(text)[0]==name


@pytest.mark.parametrize('text',['디너롤믹스','뉴디너롤36CT소스','KS마일드체다크래커','체다치즈소스'])
def test_compounds_not_collapsed_to_components(text):
    assert explicit_food_head(text) is None


def test_piece_count_is_scoped_and_not_a_package_purchase_count(monkeypatch):
    assert family('디너롤')=='빵'
    assert parse_inventory_quantity('뉴디너롤36CT','디너롤')==dict(quantity=36.0,unit='개',source='PACKAGE_TEXT')
    for text in ['뉴디너롤36','뉴디너롤36C1','뉴디너롤0CT','기타상품36CT']:
        assert parse_inventory_quantity(text,'디너롤')['source']=='DEFAULT'
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    raw='뉴디너롤36CT 5690 2 11380'
    analysis=analyze_ocr_items([dict(text=raw,confidence=.99,box=[[0,0],[600,0],[600,30],[0,30]])])
    product=analysis['products'][0]
    assert product['purchase_quantity']==2
    candidates=build_inventory_candidates(analysis)
    batch=candidates[0]['inventory_batch_candidate']
    assert batch['remaining_quantity']==72
    assert batch['unit']=='개'
    assert 'PURCHASE_QUANTITY_REQUIRES_CONFIRMATION' not in candidates[0]['warnings']

    # Package contents alone cannot establish how many packages were purchased.
    product['purchase_quantity'] = None
    product['purchase_quantity_source'] = None
    uncertain = build_inventory_candidates(analysis)[0]
    assert uncertain['inventory_batch_candidate']['remaining_quantity'] == 36
    assert uncertain['inventory_batch_candidate']['quantity_source'] == 'PACKAGE_TEXT'
    assert 'PURCHASE_QUANTITY_REQUIRES_CONFIRMATION' in uncertain['warnings']
