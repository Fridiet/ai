from app.receipt_quantity_totals import quantity_total_evidence


def row(*texts):
    return [dict(text=text, confidence=.99) for text in texts]


def example():
    return [row('상품', '1', '증정'), row('상품제로500', '1', '2,200'), row('총구 매액', '2', '2,200')]


def test_printed_counts_and_gift_balance():
    result = quantity_total_evidence(example())
    assert [result[i]['count'] for i in (0,1)] == [1,1]
    assert result[0]['transaction_type'] == 'gift'


def test_no_arithmetic_inference_of_missing_count():
    rows = example()
    rows[0] = row('상품', '증정')
    assert quantity_total_evidence(rows) == {}


def test_count_and_amount_both_must_balance():
    rows = example()
    rows[2] = row('총구매액', '3', '2,200')
    assert quantity_total_evidence(rows) == {}
    rows[2] = row('총구매액', '2', '4,400')
    assert quantity_total_evidence(rows) == {}


def test_uncertain_or_multiple_totals_are_rejected():
    rows = example()
    rows[0][1]['confidence'] = .4
    assert quantity_total_evidence(rows) == {}
    assert quantity_total_evidence(example()+[example()[-1]]) == {}


def test_pipeline_passes_verified_totals_to_receipt_response(monkeypatch):
    from app.product_pipeline import analyze_ocr_items
    from app.inventory_mapper import build_inventory_candidates
    from app.receipt_mapper import build_receipt_items
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED', 'false')
    rows = [row('양파', '1', '증정'), row('당근', '2', '2,200'), row('총구매액', '3', '2,200')]
    items = []
    for i, line in enumerate(rows):
        for j, item in enumerate(line):
            item['box'] = [[j*200,i*40],[j*200+150,i*40],[j*200+150,i*40+25],[j*200,i*40+25]]
            items.append(item)
    analysis = analyze_ocr_items(items)
    assert [(p['purchase_quantity'],p['purchase_quantity_source']) for p in analysis['products'][:2]] == [(1,'VERIFIED_RECEIPT_TOTALS'),(2,'VERIFIED_RECEIPT_TOTALS')]
    response = build_receipt_items(build_inventory_candidates(analysis))
    assert [(p['name'],p['quantity']) for p in response[:2]] == [('양파',1),('당근',2)]
