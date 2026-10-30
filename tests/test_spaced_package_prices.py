import pytest
from app.product_pipeline import extract_product_name, parse_prices, normalize_product_name, analyze_ocr_items
from app.inventory_mapper import build_inventory_candidates


@pytest.mark.parametrize('text,name',[
    ('레몬녹차(340 ml) 1,000 1 1,000','레몬녹차(340 ml)'),
    ('우유 900 ML 2,000 2 4,000','우유 900 ML'),
    ('밀가루 500 g 1,000 1 1,000','밀가루 500 g'),
    ('물 1500 ml 1,000 1 1,000','물 1500 ml'),
])
def test_contents_are_not_prices(text,name):
    assert extract_product_name(text)==name
    assert len(parse_prices(text))==2
    assert not any(unit in normalize_product_name(name).lower().split() for unit in ['ml','g'])


def test_plain_price_still_starts_price_columns():
    assert extract_product_name('양파 500 2 1000')=='양파'
    assert parse_prices('양파 500 2 1000')==[500,1000]


def test_tea_volume_and_identity_survive_together(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    monkeypatch.setattr('app.ocr_classification.predict_product',lambda _:None)
    raw=[dict(text='레몬녹차(340 ml) 1,000 1 1,000',confidence=.99,
              box=[[0,0],[500,0],[500,30],[0,30]])]
    analysis=analyze_ocr_items(raw)
    candidates=build_inventory_candidates(analysis)
    assert candidates[0]['ingredient_master_candidate']['canonical_name']=='레몬녹차'
    batch=candidates[0]['inventory_batch_candidate']
    assert (batch['remaining_quantity'],batch['unit'])==(340,'ml')


@pytest.mark.parametrize('name',['또르띠아','토르띠아','또르티야','토르티야'])
def test_tortilla_variants(monkeypatch,name):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    monkeypatch.setattr('app.ocr_classification.predict_product',lambda _:None)
    raw=[dict(text=name+' 6,000 2 12,000',confidence=.99,
              box=[[0,0],[500,0],[500,30],[0,30]])]
    result=analyze_ocr_items(raw)['products'][0]
    assert result['match']['standard_food_name']=='또띠아'
    assert result['purchase_quantity']==2
