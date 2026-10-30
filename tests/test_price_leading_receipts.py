from copy import deepcopy
from app.product_pipeline import merge_price_continuations, receipt_purchase_quantity


def rows_for(names_first=False):
    texts = ['상품명 단가 수량 금액']
    for code, name, price in [('0001000651704', '양파', '1,000 1 1,000'),
                              ('0001001618409', '당근무침350g', '4,200 1 4,200'),
                              ('0001001809708', '빵', '2,500 3 7,500')]:
        pair = [[code, *price.split()], [name]]
        texts.extend(reversed(pair) if names_first else pair)
    rows = []
    for i, line in enumerate(texts):
        words = [line] if isinstance(line, str) else line
        rows.append([dict(text=w, confidence=.99, box=[[j*220,i*40],[j*220+180,i*40],
                     [j*220+180,i*40+30],[j*220,i*40+30]]) for j,w in enumerate(words)])
    return rows


def test_price_above_name_does_not_take_next_purchase_count():
    rows = rows_for()
    original = deepcopy(rows)
    merged = merge_price_continuations(rows)
    assert rows == original
    assert len(merged) == 4
    assert [receipt_purchase_quantity(' '.join(x['text'] for x in row)) for row in merged[1:]] == [1,1,3]
    assert merged[2][0]['text'] == '당근무침350g'
    assert merged[2][1]['receipt_barcode'] is True


def test_price_below_name_keeps_existing_direction():
    merged = merge_price_continuations(rows_for(names_first=True))
    assert merged[1][0]['text'] == '양파'


def test_single_pair_does_not_select_table_layout():
    from app.product_pipeline import merge_price_leading_rows
    assert merge_price_leading_rows(rows_for()[:3]) is None
