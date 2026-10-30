import pytest
from app.receipt_name_evidence import explicit_food_head
from app.product_pipeline import analyze_ocr_items
from app.inventory_mapper import build_inventory_candidates


@pytest.mark.parametrize('text,expected',[
    ('파카디아/멸균우유1L','멸균우유'),('다른브랜드 멸균우유 200ml','멸균우유'),
    ('머쉬멜로우1359','마시멜로'),('마시멜로우135g','마시멜로'),
    ('마쉬멜로우','마시멜로'),('샤인머스켓','샤인머스캣'),('국산샤인머스캣500g','샤인머스캣'),
])
def test_complete_identity(text,expected):
    assert explicit_food_head(text)[0]==expected


@pytest.mark.parametrize('text,forbidden',[
    ('샤인머스켓음료','샤인머스캣'),('멸균우유초콜릿','멸균우유'),
    ('초코마시멜로쿠키','마시멜로'),('초코멸균우유','멸균우유'),
])
def test_compound_identity_not_component(monkeypatch,text,forbidden):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    monkeypatch.setattr('app.ocr_classification.predict_product',lambda _:None)
    result=analyze_ocr_items([dict(text=text+' 1000 1 1000',confidence=.99,
        box=[[0,0],[500,0],[500,30],[0,30]])])
    assert result['products'][0]['match']['standard_food_name']!=forbidden


def test_sterilization_does_not_infer_storage_or_damaged_grams(monkeypatch):
    from app.inventory_mapper import infer_storage_type,parse_inventory_quantity
    assert infer_storage_type('멸균우유1L','멸균우유')['storage_type']=='UNKNOWN'
    assert parse_inventory_quantity('머쉬멜로우1359','마시멜로')['source']=='DEFAULT'
