import pytest
from app.product_pipeline import extract_product_name, receipt_purchase_quantity, analyze_ocr_items


def lines(*texts):
    return [{'text': text, 'confidence': .99,
             'box': [[0, i*30], [900, i*30], [900, i*30+20], [0, i*30+20]]}
            for i, text in enumerate(texts)]


@pytest.mark.parametrize('text,expected', [
    ('003 롯데아몬드과자 990 2 1,980*', '롯데아몬드과자'),
    ('001삼립식빵768g 3,800 2 7,600', '삼립식빵768g'),
    ('002 바나나', '바나나'),
    ('79옷칠미니스푼 4,900 1 4,900', '79옷칠미니스푼'),
    ('005 파스타500g 8801045360546 3, 800 1 3,800', '파스타500g'),
    ('치즈볼100g 8809184833280 1.200 1,200', '치즈볼100g'),
])
def test_pos_index_not_price(text, expected):
    assert extract_product_name(text) == expected


@pytest.mark.parametrize('text,expected', [
    ('바나나 3,000 1 3,000#', 1),
    ('과자 990 2 1,980*', 2),
    ('감자 9, 980 1 9, 980*', 1),
    ('감자 9,980 7 9,980*', None),
    ('바나나 3,000 3,000', None),
    ('반품 -3,000 1 -3,000', None),
])
def test_count_still_requires_balanced_columns(text, expected):
    assert receipt_purchase_quantity(text) == expected


def test_multiline_product_and_numeric_prices(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED', 'false')
    result = analyze_ocr_items(lines('상품명 단가 수량 금액', '002 바나나',
                                     '202069 3,000 1 3,000#'))
    assert len(result['products']) == 1
    assert result['products'][0]['purchase_quantity'] == 1
    assert result['products'][0]['match']['standard_food_name'] == '바나나'


def test_never_merge_two_named_products():
    result = analyze_ocr_items(lines('002 바나나', '003 당근 1,000 2 2,000'))
    assert len(result['products']) == 2


def test_never_merge_total():
    result = analyze_ocr_items(lines('002 바나나', '합계 3,000 1 3,000'))
    assert result['products'][0]['purchase_quantity'] is None


def test_slightly_overlapping_slanted_boxes():
    data = lines('004 과자194g', '8801117277109 1,980 1 1,980')
    data[1]['box'] = [[0, 17], [900, 17], [900, 37], [0, 37]]
    result = analyze_ocr_items(data)
    assert len(result['products']) == 1
    assert result['products'][0]['purchase_quantity'] == 1
