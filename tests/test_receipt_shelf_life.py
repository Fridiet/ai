import asyncio
from datetime import date

import pytest

from app.receipt_shelf_life import (StorageSuggestions, apply_suggestions,
                                    fill_missing_dates, select_rows)


def items():
    return [dict(name='양파', category='vegetable', location_category='unknown',
                 consume_by_date=None, quantity=2, unit='개', raw_text='개인정보는 전송하지 않음',
                 review_notes=[])]


async def good(rows, key, model):
    assert not any('raw_text' in row or 'quantity' in row for row in rows)
    return StorageSuggestions.model_validate(dict(rows=[dict(row_id=0, storage='refrigerated', days=3)])), {'total_tokens': 30}


def test_date_and_conditional_storage_without_quantity_change():
    original = items()
    result = asyncio.run(fill_missing_dates(original, key='test', caller=good, reference_date=date(2026, 10, 4)))
    assert result[0]['consume_by_date'] == date(2026, 10, 7)
    assert result[0]['location_category'] == 'refrigerated'
    assert result[0]['quantity'] == 2 and result[0]['unit'] == '개'
    assert result[0]['raw_text'] == original[0]['raw_text']
    assert '실제 제품 소비기한' in result[0]['review_notes'][-1]
    assert original[0]['consume_by_date'] is None


@pytest.mark.parametrize('storage,days', [('refrigerated', 0),
                                        ('refrigerated', -1), ('refrigerated', 731), (None, None)])
def test_unsafe_or_uncertain_suggestions_remain_blank(storage, days):
    original = items()
    suggestions = StorageSuggestions.model_validate(dict(rows=[dict(row_id=0, storage=storage, days=days)]))
    result, count = apply_suggestions(original, select_rows(original), suggestions, date.today())
    assert count == 0 and result == original


def test_duplicate_and_unknown_row_reject_whole_response():
    original = items()
    for ids in ([0, 0], [0, 99]):
        suggestions = StorageSuggestions.model_validate(dict(rows=[dict(row_id=i, storage='refrigerated', days=2) for i in ids]))
        with pytest.raises(ValueError):
            apply_suggestions(original, select_rows(original), suggestions, date.today())


def test_existing_date_and_unclassified_name_are_not_sent():
    original = items()
    original[0]['consume_by_date'] = date(2026, 10, 6)
    assert select_rows(original) == []
    original[0].update(consume_by_date=None, name='미분류 상품')
    assert select_rows(original) == []


def test_known_storage_cannot_change():
    original = items()
    original[0]['location_category'] = 'frozen'
    suggestions = StorageSuggestions.model_validate(dict(rows=[dict(row_id=0, storage='refrigerated', days=2)]))
    assert apply_suggestions(original, select_rows(original), suggestions, date.today())[1] == 0


def test_timeout_error_disabled_and_key_absent_preserve_result():
    original = items()
    async def delayed(*args):
        await asyncio.sleep(.05)
        raise AssertionError('unreachable')
    async def failed(*args):
        raise RuntimeError('do not log this')
    for kwargs in (dict(caller=delayed, timeout=.001), dict(caller=failed),
                   dict(enabled=False, caller=failed), dict(key='', caller=failed)):
        settings = dict(key='test', **{})
        settings.update(kwargs)
        assert asyncio.run(fill_missing_dates(original, **settings)) == original


def test_invalid_strict_response_rejected():
    with pytest.raises(ValueError):
        StorageSuggestions.model_validate(dict(rows=[dict(row_id=0, storage='refrigerated', days='3')]))


@pytest.mark.parametrize('storage', ['refrigerated', 'room_temperature'])
def test_unopened_beer_gpt_days_not_capped_by_beverage_fallback(storage):
    original = items()
    original[0].update(name='맥주', category='beverage', consume_by_date=date(2026, 10, 7))
    async def beer(rows, key, model):
        assert 'max_days' not in rows[0]
        return StorageSuggestions(rows=[dict(row_id=0, storage=storage, days=180)]), {}
    result = asyncio.run(fill_missing_dates(original, key='test', caller=beer,
                         include_existing=True, reference_date=date(2026, 10, 4)))
    assert result[0]['consume_by_date'] == date(2027, 4, 2)
    assert result[0]['quantity'] == original[0]['quantity']
    assert result[0]['unit'] == original[0]['unit']
    assert original[0]['consume_by_date'] == date(2026, 10, 7)


def test_all_mode_failure_preserves_inferred_date():
    original = items()
    original[0]['consume_by_date'] = date(2026, 10, 7)
    async def failed(*args):
        raise RuntimeError('failure')
    assert asyncio.run(fill_missing_dates(original, key='test', caller=failed,
                       include_existing=True)) == original
