import pytest
from app.receipt_columns import column_anchors,reconstruct_missing_count
from app.product_pipeline import analyze_ocr_items
from app.inventory_mapper import build_inventory_candidates
from app.receipt_mapper import build_receipt_items


def box(text,x,y=40,confidence=.99):
    return dict(text=text,confidence=confidence,box=[[x-15,y],[x+15,y],[x+15,y+20],[x-15,y+20]])


def header():
    return [box('상품명',50,0),box('단가',300,0),box('수량',400,0),box('금액',500,0)]


@pytest.mark.parametrize('total,expected',[('1,000',1),('2,000',2),('2,500',None),('500',None)])
def test_requires_exact_positive_integer_ratio(total,expected):
    row=[box('당근',50),box('1,000',300),box(total,500)]
    assert reconstruct_missing_count(row,column_anchors(header()),row)==expected


@pytest.mark.parametrize('glyph',['7','1',']','?'])
def test_observed_count_even_low_confidence_is_not_overwritten(glyph):
    row=[box('당근',50),box('1,000',300),box('2,000',500)]
    assert reconstruct_missing_count(row,column_anchors(header()),row+[box(glyph,400,confidence=.1)]) is None


def test_matching_filtered_count_is_corroborated_not_replaced():
    row=[box('밀가루2.5kg',50),box('3,300',300),box('3,300',500)]
    observed=box('1',400,confidence=.30)
    assert reconstruct_missing_count(row,column_anchors(header()),row+[observed])==1
    assert reconstruct_missing_count(row,None,row+[observed]) is None
    assert reconstruct_missing_count(row,column_anchors(header()),row+[observed,observed]) is None


@pytest.mark.parametrize('glyph',['1.0','01','1?','1 1','0','100','-1'])
def test_ambiguous_count_is_not_corroborated(glyph):
    row=[box('밀가루',50),box('3,300',300),box('3,300',500)]
    assert reconstruct_missing_count(row,column_anchors(header()),row+[box(glyph,400,confidence=.2)]) is None


def test_corroborated_count_keeps_review_source(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    row=[box('밀가루2.5kg',50),box('3,300',300),box('3,300',500),box('1',400,confidence=.30)]
    product=analyze_ocr_items(header()+row)['products'][0]
    assert product['purchase_quantity']==1
    assert product['purchase_quantity_source']=='RECONSTRUCTED_PRICE_COLUMNS'


def test_requires_distinct_headers_and_high_confidence_prices():
    assert column_anchors([box('단가 수량 금액',400,0)]) is None
    row=[box('당근',50),box('1,000',300,confidence=.7),box('2,000',500)]
    assert reconstruct_missing_count(row,column_anchors(header()),row) is None


def test_duplicate_price_or_wrong_column_rejected():
    row=[box('당근',50),box('1,000',300),box('1,000',310),box('2,000',500)]
    assert reconstruct_missing_count(row,column_anchors(header()),row) is None
    assert reconstruct_missing_count([box('1,000',80),box('2,000',130)],column_anchors(header()),[]) is None


def test_api_candidate_marks_reconstructed_not_observed(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    analysis=analyze_ocr_items(header()+[box('당근',50),box('1,000',300),box('2,000',500)])
    p=analysis['products'][0]
    assert p['purchase_quantity']==2
    assert p['purchase_quantity_source']=='RECONSTRUCTED_PRICE_COLUMNS'
    items=build_receipt_items(build_inventory_candidates(analysis))
    assert items[0]['quantity']==2
    assert any('복원' in n for n in items[0]['review_notes'])
    from app.schemas import InventoryCandidatesResponse
    response=InventoryCandidatesResponse(count=1,inventory_candidates=build_inventory_candidates(analysis))
    assert response.inventory_candidates[0].inventory_batch_candidate.quantity_source=='RECONSTRUCTED_PRICE_COLUMNS'


def test_secondary_source_is_serializable_and_requires_confirmation(monkeypatch):
    from app.schemas import InventoryCandidatesResponse
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    analysis=analyze_ocr_items(header()+[box('당근',50),box('1,000',300),box('2,000',500)])
    analysis['products'][0]['purchase_quantity_source']='SECONDARY_VERIFIED_RECEIPT_COLUMNS'
    candidates=build_inventory_candidates(analysis)
    response=InventoryCandidatesResponse(count=1,inventory_candidates=candidates)
    assert response.inventory_candidates[0].inventory_batch_candidate.quantity_source=='SECONDARY_VERIFIED_RECEIPT_COLUMNS'
    assert 'PURCHASE_QUANTITY_FROM_SECONDARY_OCR_REQUIRES_CONFIRMATION' in candidates[0]['warnings']
    assert any('보조 OCR' in n for n in build_receipt_items(candidates)[0]['review_notes'])


def test_cropped_receipt_without_headers_does_not_infer_count():
    row=[box('당근',50),box('1,000',300),box('2,000',500)]
    assert analyze_ocr_items(row)['products'][0]['purchase_quantity'] is None


def test_merged_headers_require_two_balanced_rows():
    h=[box('단가 수량 금액',400,0)]
    r=[box('우유',50),box('1,000',300),box('2',400),box('2,000',500)]
    assert column_anchors(h,[r]) is None
    assert column_anchors(h,[r,r])==(300,400,500)


def test_merged_header_unstable_columns_rejected():
    h=[box('단가 수량 금액',400,0)]
    r=[box('1,000',300),box('2',400),box('2,000',500)]
    shifted=[box('1,000',230),box('2',330),box('2,000',430)]
    assert column_anchors(h,[r,shifted]) is None


def test_merged_header_single_balanced_row_needs_two_price_pairs():
    h=[box('단가 수량 금액',400,0)]
    full=[box('당근',50),box('1,000',300),box('2',400),box('2,000',500)]
    a=[box('양파',50,80),box('1,500',300,80),box('1,500',500,80)]
    b=[box('대파',50,120),box('2,000',300,120),box('6,000',500,120)]
    assert column_anchors(h,[full,a]) is None
    anchors=column_anchors(h,[full,a,b])
    assert anchors==(300,400,500)
    assert reconstruct_missing_count(b,anchors,full+a+b)==3
    assert reconstruct_missing_count(b,anchors,full+a+b+[box('7',400,120,.1)]) is None
    assert column_anchors([], [full,a,b]) is None


def test_single_balanced_row_rejects_misaligned_or_unbalanced_support():
    h=[box('단가 수량 금액',400,0)]
    full=[box('1,000',300),box('2',400),box('2,000',500)]
    a=[box('1,500',300,80),box('1,500',500,80)]
    wrong=[box('2,000',350,120),box('6,000',550,120)]
    fractional=[box('2,000',300,120),box('2,500',500,120)]
    assert column_anchors(h,[full,a,wrong]) is None
    assert column_anchors(h,[full,a,fractional]) is None
