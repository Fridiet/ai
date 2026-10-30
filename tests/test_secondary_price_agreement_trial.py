from scripts.experiment_secondary_price_agreement import reconstructed_compatible,trial_supplement


def pair():
    base=dict(normalized_name='양파',raw_text='양파 1,000 2,000',prices=[1000,2000],
              box=[[0,0],[500,0],[500,30],[0,30]],match={'standard_food_name':'양파'},
              purchase_quantity=None)
    return base,dict(base,purchase_quantity=2,purchase_quantity_source='RECONSTRUCTED_PRICE_COLUMNS')


def test_two_prices_and_secondary_arithmetic_required():
    p,s=pair()
    assert reconstructed_compatible(p,s)
    assert not reconstructed_compatible(dict(p,raw_text='양파 2,000',prices=[2000]),s)
    assert not reconstructed_compatible(dict(p,raw_text='양파 1,000 3,000',prices=[1000,3000]),s)


def test_conflicting_or_unreadable_count_cannot_be_replaced():
    p,s=pair()
    for glyph in ['7','~*','T']:
        assert not reconstructed_compatible(dict(p,raw_text=f'양파 1,000 {glyph} 2,000'),s)


def test_duplicate_primary_reserves_secondary_and_no_input_mutation():
    p,s=pair()
    primary=dict(products=[p,dict(p)])
    result=trial_supplement(primary,dict(products=[s]))
    assert all(x['purchase_quantity'] is None for x in result['products'])
    result=trial_supplement(dict(products=[p]),dict(products=[s]))
    assert result['products'][0]['purchase_quantity']==2
    assert result['products'][0]['purchase_quantity_source']=='EXPERIMENT_DUAL_PRICE_AGREEMENT'
    assert p['purchase_quantity'] is None
    assert s['purchase_quantity_source']=='RECONSTRUCTED_PRICE_COLUMNS'
