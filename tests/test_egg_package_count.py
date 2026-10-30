import pytest
from app.inventory_mapper import parse_inventory_quantity, build_inventory_candidates
from app.product_pipeline import analyze_ocr_items


@pytest.mark.parametrize('text,count',[('안심신선란30구',30),('친환경계란 10구',10),('무항생제유정란15구',15)])
def test_egg_carton_contents_are_counted_only_in_egg_context(text,count):
    assert parse_inventory_quantity(text,'달걀') == dict(quantity=float(count),unit='개',source='PACKAGE_TEXT')
    assert parse_inventory_quantity(text)['source'] == 'DEFAULT'


@pytest.mark.parametrize('text',['서울강남구', '계란찜30구','계란3.5구','계란30','계란0구'])
def test_no_generic_gu_or_missing_unit_inference(text):
    assert parse_inventory_quantity(text,'달걀')['source']=='DEFAULT'


@pytest.mark.parametrize('count', [None,2])
def test_package_contents_and_purchased_cartons_remain_distinct(monkeypatch,count):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    text='안심신선란30구 5,990' if count is None else '안심신선란30구 5,990 2 11,980'
    analysis=analyze_ocr_items([dict(text=text,confidence=.99,box=[[0,0],[500,0],[500,30],[0,30]])])
    candidate=build_inventory_candidates(analysis)[0]
    assert candidate['inventory_batch_candidate']['remaining_quantity']==(30 if count is None else 60)
    assert candidate['inventory_batch_candidate']['unit']=='개'
    assert ('PURCHASE_QUANTITY_REQUIRES_CONFIRMATION' in candidate['warnings'])==(count is None)
