import pytest
from app.product_pipeline import analyze_ocr_items, is_price_continuation, is_product_table_header, merge_price_continuations


def box(text,x,y=0,w=80):
    return dict(text=text,confidence=.99,box=[[x,y],[x+w,y],[x+w,y+20],[x,y+20]])


def test_footer_mention_is_not_header(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    items=[box('품명',0),box('단가 수량 금액',250),
           box('양파 1,000 1 1,000',0,40,500),
           box('상품명에 *가 표기됩니다',0,100,500)]
    assert not is_product_table_header([items[-1]])
    assert analyze_ocr_items(items)['products'][0]['normalized_name']=='양파'


@pytest.mark.parametrize('code',['$*211135','*211135'])
def test_internal_code_price_row_can_join_without_mutating_evidence(code):
    name=[box('008 양배추',0)]
    price=[box(code,0,30),box('980',200,30),box('1',300,30,20),box('980',400,30)]
    assert is_price_continuation(price)
    merged=merge_price_continuations([name,price])
    assert len(merged)==1
    assert merged[0][1]['receipt_barcode']
    assert 'receipt_barcode' not in price[0]
    assert analyze_ocr_items(name+price)['products'][0]['purchase_quantity']==1


@pytest.mark.parametrize('code',['$211135','$*21113','다른상품','$*2111350'])
def test_unproven_internal_codes_do_not_expand_join(code):
    assert not is_price_continuation([box(code,0),box('980',200),box('980',400)])


def test_distant_price_row_not_attached():
    rows=[[box('양파',0)], [box('$*211135',0,200),box('980',200,200),box('980',400,200)]]
    assert len(merge_price_continuations(rows))==2
