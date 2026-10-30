import pytest
from scripts.inventory_quantity_evaluation import total_equal


@pytest.mark.parametrize('quantity,unit,expected,result', [
    (12,'l',{'quantity':12000,'unit':'ml'},True),
    (.96,'kg',{'quantity':960,'unit':'g'},True),
    (2,'l',{'quantity':12000,'unit':'ml'},False),
    (12,'개',{'quantity':12,'unit':'l'},False),
    (30,'개',{'quantity':30,'unit':'개'},True),
    (1,'묶음',{'quantity':30,'unit':'개'},False),
    (True,'개',{'quantity':1,'unit':'개'},False),
    (float('nan'),'g',{'quantity':1,'unit':'g'},False),
    (None,None,{'quantity':1,'unit':'개'},False),
])
def test_physical_totals_not_package_counts(quantity,unit,expected,result):
    assert total_equal(quantity,unit,expected) is result


@pytest.mark.parametrize('count,source,supported', [(None,None,0),(1,'VERIFIED_RECEIPT_COLUMNS',1),
    (1,'NUMERIC_CELLS_VERIFIED',1)])
def test_accidentally_correct_default_is_not_evidence_backed(monkeypatch,count,source,supported):
    from scripts.audit_receipt_readiness import audit_record
    label={'filename':'synthetic','expected_items':[{'product_name':'달걀','canonical_name':'달걀',
           'is_food':True,'quantity':1,'inventory_total':{'quantity':30,'unit':'개'}}]}
    analysis={'products':[{'row_index':0,'product_name':'달걀','raw_text':'달걀',
                          'purchase_quantity':count,'purchase_quantity_source':source,'match':{'source':'test'}}]}
    monkeypatch.setattr('scripts.audit_receipt_readiness.build_inventory_candidates',lambda _: [
        {'row_index':0,'ingredient_master_candidate':{'canonical_name':'달걀'},
         'inventory_batch_candidate':{'remaining_quantity':30,'unit':'개'}}])
    result=audit_record(label,[],analysis=analysis)['counts']
    assert result['inventory_total_correct']==1
    assert result['inventory_total_with_purchase_evidence_correct']==supported


def test_correct_prediction_is_not_automatically_review_free(monkeypatch):
    from scripts.audit_receipt_readiness import audit_record
    label={'filename':'synthetic','expected_items':[{'product_name':'달걀','canonical_name':'달걀',
           'is_food':True,'quantity':1}]}
    analysis={'products':[{'row_index':0,'product_name':'달걀','raw_text':'달걀',
        'purchase_quantity':1,'purchase_quantity_source':'NUMERIC_CELLS_VERIFIED','match':{'source':'test'}}]}
    warning='PURCHASE_QUANTITY_FROM_NUMERIC_CELLS_REQUIRES_CONFIRMATION'
    monkeypatch.setattr('scripts.audit_receipt_readiness.build_inventory_candidates',lambda _: [
        {'row_index':0,'ingredient_master_candidate':{'canonical_name':'달걀'},
         'inventory_batch_candidate':{'remaining_quantity':1,'unit':'개'},'warnings':[warning]}])
    result=audit_record(label,[],analysis=analysis)
    assert result['counts']['name_and_count_correct']==1
    assert result['counts']['correct_name_count_with_confirmation_warning']==1
    assert result['rows'][0]['confirmation_warnings']==[warning]
