from copy import deepcopy
import pytest
from app.receipt_count_fusion import supplement_product_counts, needs_secondary_count_recovery


def row(count=None, name='양파', y=20):
    return dict(normalized_name=name, raw_text=name, prices=[2000],
        purchase_quantity=count, purchase_quantity_source='VERIFIED_RECEIPT_COLUMNS' if count else None,
        match={'standard_food_name':name}, box=[[0,y],[200,y],[200,y+20],[0,y+20]])


def test_fills_count_without_changing_name_or_original():
    primary={'products':[row()]}
    before=deepcopy(primary)
    result=supplement_product_counts(primary,{'products':[row(2)]})
    assert primary==before
    assert result['products'][0]['purchase_quantity']==2
    assert result['products'][0]['normalized_name']=='양파'


def test_rejects_different_row_or_name_or_price():
    for other in [row(2,y=80),row(2,name='대파'),dict(row(2),prices=[3000])]:
        assert supplement_product_counts({'products':[row()]},{'products':[other]})['products'][0]['purchase_quantity'] is None


def test_existing_counts_and_ambiguous_pairs_are_untouched():
    assert supplement_product_counts({'products':[row(1)]},{'products':[row(2)]})['products'][0]['purchase_quantity']==1
    result=supplement_product_counts({'products':[row(),row()]},{'products':[row(2)]})
    assert all(p['purchase_quantity'] is None for p in result['products'])


def test_counted_row_reserves_ambiguous_evidence():
    result = supplement_product_counts({'products': [row(2), row()]}, {'products': [row(2)]})
    assert [p['purchase_quantity'] for p in result['products']] == [2, None]


@pytest.mark.parametrize('count', [True, False, float('nan'), float('inf'), -1, 0, 100, 1.5])
def test_invalid_secondary_count_is_not_used(count):
    other = dict(row(2), purchase_quantity=count)
    assert supplement_product_counts({'products':[row()]}, {'products':[other]})['products'][0]['purchase_quantity'] is None


def test_zero_area_geometry_cannot_prove_same_row():
    other = dict(row(2), box=[[0,20],[0,20],[0,20],[0,20]])
    assert supplement_product_counts({'products':[row()]}, {'products':[other]})['products'][0]['purchase_quantity'] is None


def test_secondary_trigger_only_for_named_food_with_missing_count_and_prices():
    food = dict(row(), match={'standard_food_name': '양파', 'classification': 'ingredient'})
    assert needs_secondary_count_recovery({'products':[food]})
    for excluded in [dict(food, prices=[]), dict(food, purchase_quantity=1),
                     dict(food, match={'standard_food_name':None, 'classification':'unknown'}),
                     dict(food, match={'standard_food_name':'세제', 'classification':'nonfood'})]:
        assert not needs_secondary_count_recovery({'products':[excluded]})


def test_unbalanced_primary_digit_is_not_replaced_by_secondary():
    primary = dict(row(), raw_text='양파 2,000 3 4,000', prices=[2000,4000])
    secondary = dict(row(2), raw_text='양파 2,000 2 4,000', prices=[2000,4000])
    assert supplement_product_counts({'products':[primary]}, {'products':[secondary]})['products'][0]['purchase_quantity'] is None
