from scripts.prepare_receipt_pairs import align, key


def test_exact_short_and_identifier():
    assert key('001 양파') == key('양파')
    assert align([{'product_name': '양파'}], [{'text': '001양파'}])[0]['status'] == 'aligned'


def test_short_typo_not_forced():
    assert align([{'product_name': '고추'}], [{'text': '고츄'}])[0]['status'] != 'aligned'


def test_duplicate_printed_rows_are_ambiguous():
    result = align([{'product_name': '신선특란30구'}], [{'text': '신선특란30구'}, {'text': '신선특란30구'}])
    assert result[0]['status'] == 'alignment_review'


def test_one_ocr_span_not_reused():
    result = align([{'product_name': '신선특란30구'}]*2, [{'text': '신선특란30구'}])
    assert all(r['status'] == 'shared_ocr_span' for r in result)


def test_same_line_split_rejoined():
    boxes = [{'text': '아몬드빼빼로', 'box': [[0,0],[100,0],[100,20],[0,20]]},
             {'text': '37g', 'box': [[110,0],[150,0],[150,20],[110,20]]}]
    result = align([{'product_name': '아몬드빼빼로37g'}], boxes)[0]
    assert result['status'] == 'aligned' and result['ocr_indices'] == [0,1]
