import pytest
from app.product_pipeline import analyze_ocr_items


@pytest.mark.parametrize('name',['쥬키니','주키니','쥬키니호박','주키니호박'])
def test_zucchini_short_name_preserves_specific_identity(name,monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    result=analyze_ocr_items([dict(text=name+' 2,480 1 2,480',confidence=.99,
                                  box=[[0,0],[500,0],[500,25],[0,25]])])
    assert result['products'][0]['match']['standard_food_name']=='주키니호박'
    assert result['products'][0]['purchase_quantity']==1


def test_aehobak_not_renamed_to_zucchini(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    result=analyze_ocr_items([dict(text='애호박 1,980 1 1,980',confidence=.99,
                                  box=[[0,0],[500,0],[500,25],[0,25]])])
    assert result['products'][0]['match']['standard_food_name']=='애호박'
