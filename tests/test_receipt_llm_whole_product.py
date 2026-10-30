import copy

import pytest

from app import receipt_llm as llm
from app.ingredient_matcher import get_ingredient_catalog
from app.inventory_mapper import build_inventory_candidates
from tests.test_receipt_llm import product


@pytest.mark.parametrize('text,wrong,canonical,evidence', [
    ('농심 올리브짜파게', '올리브', '라면', '짜파게'),
    ('농심 오징어짬뽕124g', '오징어', '라면', '오징어짬뽕'),
    ('해태 포카칩', '감자', '감자칩', '포카칩'),
    ('땅콩버터', '땅콩', '땅콩버터', '땅콩버터'),
])
def test_whole_processed_identity_overrides_partial_raw_match(text, wrong, canonical, evidence):
    catalog = get_ingredient_catalog()
    original = {'products': [product(text, name=wrong, kind='contains')]}
    before = copy.deepcopy(original)
    rows = llm.select_rows(original, catalog)
    assert canonical in [c['name'] for c in rows[0]['candidates']]
    decisions = llm.Decisions(rows=[llm.Decision(row_id=0, verdict='food', candidate_id=None,
        standard_name=canonical, evidence=evidence, explanation='상품 전체 종류')])
    corrected, count = llm.apply_decisions(original, rows, decisions, catalog)
    assert count == 1 and corrected['products'][0]['match']['standard_food_name'] == canonical
    assert corrected['products'][0]['match']['classification'] == 'processed_food'
    for field in ('raw_text', 'product_name', 'prices', 'purchase_quantity', 'purchase_quantity_source'):
        assert corrected['products'][0][field] == before['products'][0][field]
    old_batch = build_inventory_candidates(original)[0]['inventory_batch_candidate']
    new_batch = build_inventory_candidates(corrected)[0]['inventory_batch_candidate']
    for field in ('remaining_quantity', 'unit', 'quantity_source'):
        assert new_batch[field] == old_batch[field]


def test_exact_alias_and_candidate_less_rows_are_reviewed():
    catalog = get_ingredient_catalog()
    rows = llm.select_rows({'products': [product('양파', row=0, name='양파', kind='exact'),
                                        product('농심 올리브짜파게', row=1, name='올리브', kind='contains'),
                                        product('xqzvvv', row=2)]}, catalog)
    assert set(r['row_id'] for r in rows) == {0, 1, 2}
    assert next(r for r in rows if r['row_id'] == 2)['candidates'] == []


def test_late_wrong_processed_row_is_prioritized_before_twenty_exact_rows():
    products = [product('양파', row=i, name='양파', kind='exact') for i in range(25)]
    products.append(product('농심 올리브짜파게', row=25, name='올리브', kind='contains'))
    rows = llm.select_rows({'products': products}, get_ingredient_catalog())
    assert len(rows) == 20 and rows[0]['row_id'] == 25


def test_standard_name_outside_hint_list_still_needs_dictionary_and_evidence():
    row = dict(row_id=0, product_text='청양고추', candidates=[])
    valid = llm.Decision(row_id=0, verdict='food', candidate_id=None,
                         standard_name='청양고추', evidence='청양고추', explanation='')
    assert llm.supported(row, valid)['name'] == '청양고추'
    assert llm.supported(row, valid.model_copy(update={'standard_name': 'GPT가만든새재료'})) is None
    assert llm.supported(row, valid.model_copy(update={'standard_name': '당근'})) is None


def test_wrong_olive_abstention_does_not_auto_register_olive():
    original = {'products': [product('농심 올리브짜파게', name='올리브', kind='contains')]}
    rows = llm.select_rows(original, get_ingredient_catalog())
    decisions = llm.Decisions(rows=[llm.Decision(row_id=0, verdict='uncertain', candidate_id=None,
                                               standard_name=None, evidence='', explanation='')])
    corrected, count = llm.apply_decisions(original, rows, decisions)
    assert corrected['products'][0]['match']['standard_food_name'] is None
    assert build_inventory_candidates(corrected) == [] and count == 0


def test_wrong_specific_mushroom_never_accepted_with_free_standard_name():
    row = dict(row_id=0, product_text='팽이버섯', candidates=[])
    bad = llm.Decision(row_id=0, verdict='food', candidate_id=None,
                       standard_name='양송이버섯', evidence='버섯', explanation='')
    assert llm.supported(row, bad) is None


@pytest.mark.parametrize('text,name', [('친환경경주미니양파', '양파'), ('송송어린대파', '대파')])
def test_whole_descriptor_evidence_supported_without_arbitrary_short_substrings(text, name):
    row = dict(row_id=0, product_text=text, candidates=[])
    decision = llm.Decision(row_id=0, verdict='food', candidate_id=None,
                            standard_name=name, evidence=text, explanation='')
    assert llm.supported(row, decision)['name'] == name


def test_confirmation_preserves_exact_match_and_does_not_fake_probability():
    original = {'products': [product('양파', name='양파', kind='exact')]}
    rows = llm.select_rows(original, get_ingredient_catalog())
    choice = next(c for c in rows[0]['candidates'] if c['name'] == '양파')
    decision = llm.Decision(row_id=0, verdict='food', candidate_id=choice['id'],
                            standard_name='양파', evidence='양파', explanation='')
    corrected, count = llm.apply_decisions(original, rows, llm.Decisions(rows=[decision]))
    assert count == 0 and corrected['products'][0]['match'] == original['products'][0]['match']


def test_partial_response_is_rejected_instead_of_silently_marked_reviewed():
    original = {'products': [product('양파', row=0), product('대파', row=1)]}
    rows = llm.select_rows(original, get_ingredient_catalog())
    with pytest.raises(ValueError):
        llm.apply_decisions(original, rows, llm.Decisions(rows=[]))


def test_olive_oil_alias_cannot_become_raw_olives():
    catalog = get_ingredient_catalog()
    row = dict(row_id=0, product_text='올리브오일', candidates=llm.candidates('올리브오일', catalog))
    bad = llm.Decision(row_id=0, verdict='food', candidate_id=None, standard_name='올리브',
                       evidence='올리브오일', explanation='')
    assert llm.supported(row, bad) is None


def test_separate_validated_barcode_does_not_block_product_review():
    item = product('농심올리브짜파게티1개', name='올리브', kind='contains')
    item['raw_text'] = '002 농심올리브짜파게티1개 8801043015226 1,200 1 1,200'
    item['items'] = [dict(text='8801043015226', receipt_barcode=True)]
    rows = llm.select_rows({'products': [item]}, get_ingredient_catalog())
    assert len(rows) == 1
    assert '8801043015226' not in str(rows)
    assert item['raw_text'].endswith('1,200 1 1,200')


@pytest.mark.parametrize('value,flag,label', [
    ('8801043015226', False, ''), ('8801043015225', True, ''),
    ('8801043015226', True, '카드번호 '), ('010-1234-5678', True, ''),
])
def test_barcode_exception_cannot_bypass_private_labels_or_invalid_codes(value, flag, label):
    item = product('양파')
    item['raw_text'] = '양파 ' + label + value + ' 3,980 1 3,980'
    item['items'] = [dict(text=value, receipt_barcode=flag)]
    assert llm.product_text(item) is None
