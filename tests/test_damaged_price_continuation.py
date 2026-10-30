import pytest
from app.product_pipeline import is_price_continuation, merge_price_continuations, receipt_purchase_quantity


def box(text,x,y=45,w=70):
    return {'text':text,'confidence':.99,'box':[[x,y],[x+w,y],[x+w,y+30],[x,y+30]]}


@pytest.mark.parametrize('code,count', [('2C00001368343','T'),('2000000969008','T--'),('8809188241005','9p')])
def test_damaged_barcode_or_count_can_join_but_is_not_repaired(code,count):
    line=[box(code,0,w=150),box('2,980',250),box(count,350,w=30),box('2,980',450)]
    assert is_price_continuation(line)
    merged=merge_price_continuations([[box('해시브라운',0,y=0,w=200)],line])
    assert len(merged)==1
    raw=' '.join(i['text'] for i in merged[0])
    assert count in raw
    assert receipt_purchase_quantity(raw) is None
    assert merged[0][1].get('receipt_barcode') is True
    assert 'receipt_barcode' not in line[0]


@pytest.mark.parametrize('extra', [box('다른상품',0,w=150),box('T',550),box('ABCDEF123',0,w=150)])
def test_does_not_join_other_product_or_unpositioned_glyph(extra):
    assert not is_price_continuation([extra,box('2,980',250),box('2,980',450)])


def test_two_possible_counts_are_ambiguous():
    line=[box('2000000969008',0,w=150),box('2,980',250),box('T',345,w=10),box('I',370,w=10),box('2,980',450)]
    assert not is_price_continuation(line)


def test_uncertain_price_and_count_attach_without_fabricating_count():
    line=[box('8809400410684',0,w=150),box('4,980',250),box('~*',350,w=30),box('4,980',450)]
    line[-1]['confidence']=.875
    assert is_price_continuation(line)
    merged=merge_price_continuations([[box('다진마늘1kg',0,y=0,w=200)],line])
    assert len(merged)==1
    assert receipt_purchase_quantity(' '.join(i['text'] for i in merged[0])) is None
    assert merged[0][-1]['confidence']==.875
    line[-1]['confidence']=.79
    assert not is_price_continuation(line)


def test_percent_suffix_is_preserved_not_interpreted_as_currency_evidence():
    line=[box('5900120015838',0,w=150),box('1,990',250),box('2',350,w=30),box('3,980%',450)]
    merged=merge_price_continuations([[box('멸균우유1L',0,y=0,w=200)],line])
    assert len(merged)==1
    raw=' '.join(i['text'] for i in merged[0])
    assert '3,980%' in raw
    assert receipt_purchase_quantity(raw) is None


def test_percent_discount_without_barcode_cannot_attach():
    assert not is_price_continuation([box('1,990',250),box('2',350,w=30),box('3,980%',450)])
