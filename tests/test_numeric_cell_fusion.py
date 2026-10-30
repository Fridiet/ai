from app.receipt_numeric_cells import supplement_numeric_cells


def evidence():
    p=dict(normalized_name='드레싱',raw_text='드레싱 3,780',prices=[3780],
           box=[[0,0],[500,0],[500,30],[0,30]],match={'standard_food_name':'드레싱'},purchase_quantity=None)
    s=dict(p,raw_text='드레싱 3,780 3,780',prices=[3780,3780],purchase_quantity=1,
           purchase_quantity_source='RECONSTRUCTED_PRICE_COLUMNS')
    cells=[dict(row=0,margin=0,field=k,text=t,confidence=.99,secondary=s['raw_text'])
           for k,t in [('unit_price','3,780'),('count','1'),('total','3,780')]]
    return p,s,cells


def test_missing_count_recovered_with_distinct_source():
    p,s,cells=evidence()
    result=supplement_numeric_cells({'products':[p]},{'products':[s]},cells)
    assert result['products'][0]['purchase_quantity']==1
    assert result['products'][0]['purchase_quantity_source']=='NUMERIC_CELLS_VERIFIED'
    assert p['purchase_quantity'] is None


def test_secondary_conflicting_glyph_and_stale_cells_block_recovery():
    for text in ['드레싱 3,780 7 3,780','다른상품 3,780 3,780']:
        p,s,cells=evidence();s['raw_text']=text
        if '7' in text.split():
            for c in cells:c['secondary']=text
        assert supplement_numeric_cells({'products':[p]},{'products':[s]},cells)['products'][0]['purchase_quantity'] is None


def test_existing_primary_and_duplicate_row_are_preserved():
    p,s,cells=evidence()
    duplicate=supplement_numeric_cells({'products':[p,dict(p)]},{'products':[s]},cells)
    assert all(x['purchase_quantity'] is None for x in duplicate['products'])
    p.update(purchase_quantity=2,purchase_quantity_source='VERIFIED_RECEIPT_COLUMNS')
    assert supplement_numeric_cells({'products':[p]},{'products':[s]},cells)['products'][0]['purchase_quantity']==2


def test_new_source_serializes_with_review_note(monkeypatch):
    from app.product_pipeline import analyze_ocr_items
    from app.inventory_mapper import build_inventory_candidates
    from app.receipt_mapper import build_receipt_items
    from app.schemas import InventoryCandidatesResponse
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    analysis=analyze_ocr_items([dict(text='양파 1,000 1 1,000',confidence=.99,
                                   box=[[0,0],[500,0],[500,30],[0,30]])])
    analysis['products'][0]['purchase_quantity_source']='NUMERIC_CELLS_VERIFIED'
    candidates=build_inventory_candidates(analysis)
    response=InventoryCandidatesResponse(count=1,inventory_candidates=candidates)
    assert response.inventory_candidates[0].inventory_batch_candidate.quantity_source=='NUMERIC_CELLS_VERIFIED'
    assert any('다시 읽고' in note for note in build_receipt_items(candidates)[0]['review_notes'])
