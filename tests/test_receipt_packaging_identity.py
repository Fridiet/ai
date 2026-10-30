import pytest
from app.receipt_name_evidence import fresh_suffix, explicit_food_head
from app.product_pipeline import analyze_ocr_items
from app.inventory_mapper import parse_inventory_quantity, build_inventory_candidates
from app.receipt_mapper import build_receipt_items


@pytest.mark.parametrize('name', ['바나나1묶음', '바나나(1묶음)', '바나나 2묶음'])
def test_bundle_suffix_is_not_part_of_produce_name(name):
    assert fresh_suffix(name)=='바나나'


@pytest.mark.parametrize('name', ['바나나맛과자1묶음','바나나1볶음','바나나우유1묶음'])
def test_does_not_correct_cooked_or_flavored_product_to_bundle(name):
    assert fresh_suffix(name) is None


@pytest.mark.parametrize('name,expected', [('맑은샘물2.0L*6','물'),('먹는샘물500ml×20','물'),
                                         ('삼립 땅콩가득꿀호떡6','호떡')])
def test_packaging_does_not_hide_explicit_product(name,expected):
    assert explicit_food_head(name)[0]==expected


def test_water_multipack_does_not_become_purchase_count(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    row={'text':'맑은샘물2.0L*6 2,000 1 2,000','confidence':.99,
         'box':[[0,0],[700,0],[700,30],[0,30]]}
    product=analyze_ocr_items([row])['products'][0]
    assert product['match']['standard_food_name']=='물'
    assert product['purchase_quantity']==1
    receipt=build_receipt_items(build_inventory_candidates(analyze_ocr_items([row])))
    assert receipt[0]['quantity']==12
    assert receipt[0]['unit']=='l'


@pytest.mark.parametrize('name', ['먹는샘물향샴푸','호떡6개믹스','호떡쿠키'])
def test_other_product_heads_are_not_water_or_hottek(name):
    head=explicit_food_head(name)
    assert head is None or head[0] not in {'물','호떡'}


@pytest.mark.parametrize('text,quantity', [('물2.0L*6',12),('생수500ml×20',10000),('두유190ml x 24입',4560),('생수500mlx6',3000)])
def test_explicit_package_multiplier(text,quantity):
    assert parse_inventory_quantity(text)['quantity']==quantity


@pytest.mark.parametrize('text', ['물2L*?', '물2L*0', '물2L*6+2', '물2L*6.5'])
def test_ambiguous_bundle_total_requires_confirmation(text):
    assert parse_inventory_quantity(text)['source']=='DEFAULT'
