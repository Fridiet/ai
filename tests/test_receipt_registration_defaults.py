import asyncio
from datetime import date, timedelta

import pytest

from app.receipt_shelf_life import fill_missing_dates, finalize_registration_items


def item(**changes):
    result = dict(name='양파', category='vegetable', consume_by_date=None,
                  location_category='unknown', quantity=2, unit='개', raw_text='양파 2개',
                  review_notes=['추정값 안내', 'GPT 보정 안내', '수량 안내'])
    result.update(changes)
    return result


@pytest.mark.parametrize('category,days', [('vegetable',7), ('meat',3), ('seafood',2),
                                        ('dairy',7), ('egg',21), ('seasoning',60), ('etc',7)])
def test_defaults_always_supply_conditional_date_without_repeated_notes(category, days):
    original = item(category=category)
    today = date(2026, 10, 4)
    result = finalize_registration_items([original], reference_date=today)[0]
    assert result['consume_by_date'] == today + timedelta(days=days)
    assert result['location_category'] == 'refrigerated'
    assert result['review_notes'] == []
    for field in ('name', 'quantity', 'unit', 'raw_text'):
        assert result[field] == original[field]
    assert original['consume_by_date'] is None and len(original['review_notes']) == 3


def test_existing_date_and_storage_are_not_overwritten():
    original = item(consume_by_date=date(2026, 10, 8), location_category='frozen')
    result = finalize_registration_items([original])[0]
    assert result['consume_by_date'] == original['consume_by_date']
    assert result['location_category'] == 'frozen'


def test_missing_key_disabled_or_failed_gpt_still_has_date_in_public_result():
    async def broken(*args):
        raise RuntimeError('failure')
    for settings in (dict(key=''), dict(key='test', enabled=False), dict(key='test', enabled=True, caller=broken)):
        reviewed = asyncio.run(fill_missing_dates([item()], **settings))
        final = finalize_registration_items(reviewed)
        assert final[0]['consume_by_date'] and final[0]['review_notes'] == []
