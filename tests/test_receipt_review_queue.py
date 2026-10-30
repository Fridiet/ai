from scripts.build_new_receipt_review_queue import store_hint


def lines(*texts):
    return [dict(text=t,box=[[0,i*20],[100,i*20],[100,i*20+10],[0,i*20+10]]) for i,t in enumerate(texts)]


def test_bank_name_does_not_label_daiso_as_grocery():
    assert store_hint(lines('다이소','농협은행'))=='household_hint'
    assert store_hint(lines('농협은행'))=='needs_visual_review'


def test_convenience_before_supermarket_and_product_body_ignored():
    assert store_hint(lines('이마트24'))=='convenience_hint'
    assert store_hint(lines('이마트 청주점'))=='grocery_hint'
    assert store_hint(lines(*(['문구']*8), '이마트'))=='needs_visual_review'
