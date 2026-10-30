from app.receipt_sku_rows import merge_sku_rows,numeric_count


def row(text,y):
    return [dict(text=text,confidence=.99,box=[[0,y],[500,y],[500,y+20],[0,y+20]])]


def test_repeated_sku_layout_and_coupon_do_not_mutate_raw():
    rows=[row('먹는샘물2L X 6',0),row('568800 2 1770 3,540 T',30),
          row('WHEAT TORTILLAS',60),row('592373 1 16490 16,490 T',90),
          row('자사 쿠폰',120),row('통밀또띠아 IRC',150),row('015326 r 2500 2,500-T',180)]
    merged=merge_sku_rows(rows)
    assert len(merged)==3
    assert merged[0][0]['sku_count_verified']==2
    assert merged[0][1]['receipt_barcode']
    assert all(i['receipt_adjustment'] for i in merged[2])
    assert 'sku_count_verified' not in rows[0][0]


def test_single_pair_does_not_select_layout():
    rows=[row('상품',0),row('568800 2 1770 3,540 T',30)]
    assert merge_sku_rows(rows) is rows


def test_numeric_evidence_rejects_bad_counts_and_refunds():
    assert numeric_count(row('568800 2 1770 3,540T',0))==2
    assert numeric_count(row('568800 2 1770 3,540 T',0))==2
    for text in ['568800 7 1770 3,540 T','568800 r 1770 3,540 T',
                 '568800 2 1770 3,540-T','568800 2 1770 3,540',
                 '568800 2 1770 3,540 T extra']:
        assert numeric_count(row(text,0)) is None
    low=row('568800 2 1770 3,540 T',0)
    low[0]['confidence']=.89
    assert numeric_count(low) is None


def test_pipeline_preserves_name_and_verified_count(monkeypatch):
    from app.product_pipeline import analyze_ocr_items
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    rows=[row('먹는샘물2L X 6',0),row('568800 2 1770 3,540 T',30),
          row('당근',60),row('592373 1 1000 1,000 T',90),
          row('자사 쿠폰',120),row('통밀또띠아 IRC',150),row('015326 1 2500 2,500-T',180)]
    result=analyze_ocr_items([i for r in rows for i in r])
    assert len(result['products'])==2
    assert [p['purchase_quantity'] for p in result['products']]==[2,1]
    assert all(p['purchase_quantity_source']=='VERIFIED_RECEIPT_COLUMNS' for p in result['products'])
    assert '568800' not in result['products'][0]['product_name']
    assert '568800' in result['products'][0]['raw_text']


def test_english_tortilla_name_is_whole_product_not_chips():
    from app.receipt_name_evidence import explicit_food_head
    assert explicit_food_head('WHEAT TORTILLAS')==('또띠아','가공식품')
    assert explicit_food_head('wheat tortilla')==('또띠아','가공식품')
    for text in ['WHEAT TORTILLA CHIPS','WHEAT FLOUR','WHEAT TORTILLA MIX']:
        assert explicit_food_head(text) is None


def test_damaged_numeric_line_is_retained_without_verified_count(monkeypatch):
    from app.product_pipeline import analyze_ocr_items
    from app.receipt_mapper import build_review_candidates
    monkeypatch.setattr('app.product_pipeline.apply_classification',lambda *args:dict(
        standard_food_name=None,classification='unknown',source='test'))
    rows=[row('상품가',0),row('568800 2 1770 3,540T',30),
          row('상품나',60),row('592373 1 1000 1,000 T',90),
          row('CRISP SPICY 2 KG',120),row('5756:69 1 8990 8,990 T',150),
          row('WHEAT TORTILLAS',180),row('592373 7 16490 16,490 T',210)]
    analysis=analyze_ocr_items([i for r in rows for i in r])
    assert len(analysis['products'])==4
    assert all(p['purchase_quantity'] is None for p in analysis['products'][2:])
    review=build_review_candidates(analysis)
    assert any('CRISP SPICY' in p['rawText'] and p['purchaseCount'] is None for p in review)
