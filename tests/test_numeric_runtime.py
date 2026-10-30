import numpy as np
import pytest
from app import receipt_numeric_runtime as runtime


def setup_rows(monkeypatch, count=1):
    monkeypatch.setattr(runtime, 'clean_ocr_items', lambda x:x)
    monkeypatch.setattr(runtime, 'group_product_rows', lambda x:x)
    monkeypatch.setattr(runtime, 'merge_price_continuations', lambda x:x)
    monkeypatch.setattr(runtime, 'is_product_table_header', lambda x:True)
    monkeypatch.setattr(runtime, 'column_anchors', lambda *args:(200, 300, 400))
    products=[]
    for i in range(count):
        products.append(dict(normalized_name=f'상품{i}', raw_text=f'상품{i} 1000 1000',
            prices=[1000,1000], purchase_quantity=None,
            match={'standard_food_name':f'상품{i}'},
            box=[[0, i*30], [450,i*30], [450,i*30+20], [0,i*30+20]]))
    return {'products':products}


def test_only_missing_rows_and_bounded_batch(monkeypatch):
    analysis=setup_rows(monkeypatch, 7)
    image=np.zeros((300,500,3),dtype=np.uint8)
    crops, metadata=runtime.prepare_numeric_crops(image, analysis, analysis, [{}])
    assert len(crops)==len(metadata)==12
    assert {x['row'] for x in metadata}=={0,1,2,3}
    analysis['products'][0]['purchase_quantity']=1
    _, metadata=runtime.prepare_numeric_crops(image, analysis, analysis, [{}])
    assert {x['row'] for x in metadata}=={1,2,3,4}


def test_missing_header_skips_recognizer(monkeypatch):
    analysis=setup_rows(monkeypatch)
    monkeypatch.setattr(runtime, 'is_product_table_header', lambda x:False)
    from app import ocr
    monkeypatch.setattr(ocr, 'recognize_numeric_crops', lambda x:pytest.fail('Should not run'))
    assert runtime.recover_numeric_counts(np.zeros((40,500,3)),analysis,analysis,[{}]) is analysis


def test_reads_and_merges_without_mutating_primary(monkeypatch):
    analysis=setup_rows(monkeypatch)
    from app import ocr
    monkeypatch.setattr(ocr, 'recognize_numeric_crops', lambda x:[
        {'text':t,'confidence':.99} for t in ['1000','1','1000']])
    result=runtime.recover_numeric_counts(np.zeros((40,500,3)),analysis,analysis,[{}])
    assert result['products'][0]['purchase_quantity']==1
    assert result['products'][0]['purchase_quantity_source']=='NUMERIC_CELLS_VERIFIED'
    assert analysis['products'][0]['purchase_quantity'] is None


def test_misaligned_predictions_fail_closed(monkeypatch):
    analysis=setup_rows(monkeypatch)
    from app import ocr
    monkeypatch.setattr(ocr, 'recognize_numeric_crops', lambda x:[])
    with pytest.raises(ValueError,match='result count mismatch'):
        runtime.recover_numeric_counts(np.zeros((40,500,3)),analysis,analysis,[{}])
