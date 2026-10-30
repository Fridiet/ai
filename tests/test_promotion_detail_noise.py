import pytest
from app.product_pipeline import is_adjustment_row


@pytest.mark.parametrize('text', ['[M+N]11-1덤 3,980', '[M+N]111덤 3,980',
                                  '[M + N] 1+1덤 3,980', '[M+N]1+1덤 -3,980'])
def test_damaged_promotion_only_rows(text):
    assert is_adjustment_row(text)


@pytest.mark.parametrize('text', ['[M+N] 호떡 3,980 1 3,980',
                                  '삼립 1+1 호떡 3,980 1 3,980',
                                  '[M+N]111덤 호떡 3,980', '[M+N]111 3,980'])
def test_product_text_is_not_discarded(text):
    assert not is_adjustment_row(text)
