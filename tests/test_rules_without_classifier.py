import pytest
from app.product_pipeline import analyze_ocr_items
from app import ocr_classification


@pytest.mark.parametrize('text,expected',[
    ('파카디아 멸균우유1L','멸균우유'),('레몬녹차(340 ml)','레몬녹차'),
    ('또르띠아','또띠아'),('샤인머스켓','샤인머스캣'),
    ('친환경복주머니양파','양파'),('송송어린대파','대파'),
    ('초코마시멜로쿠키','쿠키'),
    ('샤인머스켓음료',None),('멸균우유초콜릿',None),
    ('국산마늘샴푸',None),
])
def test_rules_remain_active_without_model(monkeypatch,text,expected):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    monkeypatch.setattr(ocr_classification,'_load',lambda:pytest.fail('Disabled model loaded'))
    ocr_classification.warmup()
    products=analyze_ocr_items([dict(text=text+' 1,000 1 1,000',confidence=.99,
        box=[[0,0],[500,0],[500,30],[0,30]])])['products']
    assert products[0]['match']['standard_food_name']==expected


def test_unrecognized_text_does_not_load_disabled_model(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    monkeypatch.setattr(ocr_classification,'_load',lambda:pytest.fail('Disabled model loaded'))
    assert ocr_classification.predict_product('처음보는모호한상품') is None
