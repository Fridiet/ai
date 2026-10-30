import copy

import pytest

from app.receipt_mapper import build_review_candidates


@pytest.mark.parametrize('details', [
    {},
    {'llm_review_reason': 'GPT 보정으로도 재료명을 확정하지 못했습니다. 상품명을 확인해주세요.'},
    {'llm_review_reason': '가공식품 문맥과 기존 재료명이 충돌합니다. 상품 종류를 확인해주세요.'},
    {'secondary_name_hint': {'canonical_name': '청양고추'}},
])
def test_public_reason_concise_but_internal_evidence_preserved(details):
    analysis = {'products': [dict(raw_text='대림 크랩킹버터갈릭 6,980 1 6,980',
        product_name='대림 크랩킹버터갈릭', purchase_quantity=1, prices=[6980],
        match={'standard_food_name': None, 'classification': 'unknown'}, **details)]}
    original = copy.deepcopy(analysis)
    review = build_review_candidates(analysis)
    assert review == [dict(rawText=original['products'][0]['raw_text'], purchaseCount=1,
                           reason='재료명을 확인해주세요.')]
    assert analysis == original
