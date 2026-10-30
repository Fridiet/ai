import pytest
from app.product_pipeline import analyze_ocr_items
from app.inventory_mapper import build_inventory_candidates
from app.receipt_mapper import build_receipt_items


@pytest.mark.parametrize('name,source,count,informational', [
    ('양파','VERIFIED_RECEIPT_COLUMNS',2,True),
    ('양파','VERIFIED_RECEIPT_TOTALS',2,True),
    ('양파','RECONSTRUCTED_PRICE_COLUMNS',2,False),
    ('양파','SECONDARY_VERIFIED_RECEIPT_COLUMNS',2,False),
    ('양파','NUMERIC_CELLS_VERIFIED',2,False),
    ('양파',None,None,False),
    ('우유900m1','VERIFIED_RECEIPT_COLUMNS',2,False),
    ('양파500','VERIFIED_RECEIPT_COLUMNS',2,False),
])
def test_purchase_units_not_unknown_contents(monkeypatch,name,source,count,informational):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    analysis=analyze_ocr_items([dict(text='양파 1000 2 2000',confidence=.99,
        box=[[0,0],[500,0],[500,30],[0,30]])])
    analysis['products'][0].update(product_name=name,purchase_quantity=count,purchase_quantity_source=source)
    candidates=build_inventory_candidates(analysis)
    warnings=candidates[0]['warnings']
    assert ('PURCHASE_UNIT_COUNT_ONLY' in warnings)==informational
    assert ('PACKAGE_SIZE_REQUIRES_CONFIRMATION' in warnings)==(not informational)
    batch=candidates[0]['inventory_batch_candidate']
    assert batch['unit']=='개'
    assert batch['remaining_quantity']==(count or 1)
    item=build_receipt_items(candidates)[0]
    assert any('묶음 안의 낱개' in note for note in item['review_notes'])==informational
