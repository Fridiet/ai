import pytest
from app.inventory_mapper import parse_inventory_quantity, build_inventory_candidates
from app.product_pipeline import analyze_ocr_items


@pytest.mark.parametrize('suffix',['*4+2입','4+2입','×(4+2)입','x(4+2입)','*4+2개입'])
def test_explicit_additive_bundle_size(suffix):
    assert parse_inventory_quantity('플레인요거트85g'+suffix)==dict(quantity=510.,unit='g',source='PACKAGE_TEXT')


@pytest.mark.parametrize('suffix',['*4+2','4+2','*4+2입 할인','*(4+2입','*4+2)입',
                                    '*0+2입','*4+-2입','*4+2.5입','*999+2입','*4+2입+1입'])
def test_ambiguous_or_invalid_expression_requires_review(suffix):
    assert parse_inventory_quantity('요거트85g'+suffix)['source']=='DEFAULT'


def test_purchase_count_separate_from_six_containers(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    analysis=analyze_ocr_items([dict(text='요거트85g4+2입 2,990 2 5,980',confidence=.99,
                                   box=[[0,0],[700,0],[700,30],[0,30]])])
    assert analysis['products'][0]['purchase_quantity']==2
    batch=build_inventory_candidates(analysis)[0]['inventory_batch_candidate']
    assert batch['remaining_quantity']==1020 and batch['unit']=='g'


def test_promotion_before_product_does_not_multiply_package():
    assert parse_inventory_quantity('1+1 행사 요거트85g')['quantity']==85
