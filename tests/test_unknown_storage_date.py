from datetime import date
from app.receipt_mapper import recommended_consume_by_date
from app.schemas import ReceiptInventoryItem


def test_unknown_storage_has_no_invented_date():
    for storage in ('unknown', '', 'unsupported'):
        assert recommended_consume_by_date(category='beverage', location_category=storage,
                                            reference_date=date(2026,9,12)) is None


def test_known_storage_retains_explicitly_estimated_date():
    assert recommended_consume_by_date(category='vegetable', location_category='refrigerated',
                                      reference_date=date(2026,9,12)) == date(2026,9,19)


def test_receipt_schema_serializes_unknown_date_as_null():
    item = ReceiptInventoryItem(name='사이다',quantity=1,unit='개',consumeByDate=None,
                                locationCategory='unknown',category='beverage')
    assert item.model_dump(by_alias=True,mode='json')['consumeByDate'] is None


def test_published_schema_accepts_date_or_null():
    schema = ReceiptInventoryItem.model_json_schema(by_alias=True)
    variants = schema['properties']['consumeByDate']['anyOf']
    assert {'type':'null'} in variants
    assert {'type':'string','format':'date'} in variants
