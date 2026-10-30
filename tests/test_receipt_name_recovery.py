from copy import deepcopy
import pytest
from app.receipt_name_recovery import propose_names


def pair():
    base={'product_name':'관튼록친즌핀자','raw_text':'관튼록친즌핀자 6,990 2 13,980',
          'prices':[6990,13980],'purchase_quantity':2,'purchase_quantity_source':'VERIFIED_RECEIPT_COLUMNS',
          'box':[[0,0],[700,0],[700,40],[0,40]],'match':{'standard_food_name':None,'classification':'unknown'}}
    second=dict(base,product_name='콰트로치즈피자',raw_text='콰트로치즈피자 6,990 2 13,980',
                match={'standard_food_name':'피자','source':'receipt_food_head_rule'},
                items=[{'text':'콰트로치즈피자','confidence':.99}])
    return {'products':[base]},{'products':[second]}


def test_strong_name_proposal_retains_original():
    a,b=pair(); before=deepcopy(a)
    proposal=propose_names(a,b)
    assert proposal[0]['canonical_name']=='피자'
    assert proposal[0]['requires_confirmation'] is True
    assert a==before


@pytest.mark.parametrize('case',['count','price','low_name_confidence','named','nonfood','ambiguous','other_row','model_only','numeric_confidence'])
def test_rejects_uncertain_correspondence(case):
    a,b=pair()
    if case=='count': b['products'][0]['purchase_quantity']=1
    if case=='price': b['products'][0]['prices']=[1000,2000]
    if case=='low_name_confidence': b['products'][0]['items'][0]['confidence']=.9
    if case=='named': a['products'][0]['match']['standard_food_name']='치즈'
    if case=='nonfood': a['products'][0]['match']['classification']='nonfood'
    if case=='ambiguous': a['products'].append(deepcopy(a['products'][0]))
    if case=='other_row': b['products'][0]['box']=[[0,60],[700,60],[700,100],[0,100]]
    if case=='model_only': b['products'][0]['match']['source']='ocr_classifier_fast_v7'
    if case=='numeric_confidence': b['products'][0]['items']=[{'text':'관튼록친즌핀자','confidence':.75},{'text':'13,980','confidence':1}]
    assert propose_names(a,b)==[]
