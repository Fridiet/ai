import pytest
from app.receipt_count_fusion import compatible


@pytest.mark.parametrize('marker',['%','*#','#*','%%'])
def test_marker_cannot_hide_conflicting_observed_count(marker):
    p=dict(normalized_name='멸균우유',raw_text='멸균우유 1,990 7 3,980'+marker,
        prices=[1990,3980],box=[[0,0],[500,0],[500,30],[0,30]])
    s=dict(p,purchase_quantity=2,purchase_quantity_source='VERIFIED_RECEIPT_COLUMNS')
    assert not compatible(p,s)
